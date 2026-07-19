"""
Phase 1 — read-only P&L diagnostic for live Binance Futures runs.

Reads data/journal/signals.json and answers the question
"why is realized P&L tending to zero with mostly SL hits?" without
touching any production code.

Outputs:
  1. Top-line counters (real fills, OPEN/WIN/LOSS/BE, rejection reasons).
  2. Per-pair, per-direction realized R distribution (mean, median,
     sum). R is computed against the journal's stored entry/sl prices
     — same formula as Order.realised_pnl_R, which is the live router's
     truth source today.
  3. **Close-reason breakdown**. Classifies each closed row by whether
     the recorded closed_price matched tp / sl / entry to within a
     tolerance. closed_price == entry means the broker's _finalize_filled
     fell through to "MANUAL" — a zero-R close that pollutes the
     average and is a likely contributor to the "P&L tending to zero"
     symptom.
  4. Implied USDT P&L at both RISK_PCT_LIVE (intended) and RISK_PCT
     (what scanner.py:239 actually uses when SHADOW_MODE=false). Shows
     the 10x silent-oversizing impact.
  5. Per-pair daily SL-hit / TP-hit / BE counts.
  6. Off-trigger flag: rows where closed_price ∉ {tp, sl, entry} within
     tolerance. Those are real broker fills that hit something else
     (early manual cancel, partial fill bridge, …).

Usage:
    python scripts/diagnose_live_pnl.py
    python scripts/diagnose_live_pnl.py --json > data/diagnostics.json
    python scripts/diagnose_live_pnl.py --bps-tol 5   # bps tolerance
                                                       # for the close-
                                                       # reason classifier
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = REPO_ROOT / "data" / "journal" / "signals.json"


def _bps(actual: float, ref: float) -> float:
    """Signed deviation in basis points: (actual - ref) / ref * 10_000."""
    if ref == 0:
        return float("inf")
    return (actual - ref) / ref * 10_000.0


def _classify_close(row: dict, bps_tol: float) -> str:
    """Bucket a closed row's recorded closed_price by which leg it hit.

    Tolerance is symmetric in bps. Order: tp, sl, entry, then other.
    The journal stores no actual fill price, so this is a proxy: a
    closed_price exactly == sl means the SL trigger fired (or the
    synthetic settle_open_signals path used sl as the fallback price).
    """
    cp = row.get("closed_price")
    if cp is None:
        return "no_close_price"
    for label, ref in (("tp", row.get("tp")), ("sl", row.get("sl")), ("entry", row.get("price"))):
        if ref is None:
            continue
        if abs(_bps(float(cp), float(ref))) <= bps_tol:
            return label
    return "other"


def _classify_truth(row: dict) -> str:
    """Fix 2.J (plan: live P&L clean-up follow-up): orthogonal to the
    leg-match classifier — tells you whether a closed row came from
    real broker truth or the synthetic bar-OHLC settler.

    Buckets (all assume row is a closed BUY/SELL — caller filters):

    - `broker-truth`     : row has `pnl_r` populated AND broker tag is
                           non-paper AND `closed_price` is NOT
                           bit-for-bit equal to tp/sl. Phase 2's
                           Fix 2.B + 2.E + 2.F succeeded end-to-end.
    - `broker-truth-no-fee` : live broker truth with `fees_paid` missing
                              (close fee fetch failed; gross R is the
                              best we can offer for this row).
    - `synthetic-paper`  : no `pnl_r`, closed_price bit-for-bit on tp/sl,
                           broker is paper or missing. Expected for
                           paper rows + pre-Fix-2.A legacy rows.
    - `synthetic-live-bug` : no `pnl_r`, closed_price bit-for-bit on
                             tp/sl, broker is non-paper. The regression
                             Phase 2 fixed; new live rows must NEVER
                             land here. Phase 3 Layer 2 acceptance
                             gate.
    - `partial`          : closed row but the classifier can't bucket
                           it cleanly (missing fields, malformed row).
    """
    cp = row.get("closed_price")
    if cp is None:
        return "partial"
    broker = row.get("broker", "paper") or "paper"
    pnl_r = row.get("pnl_r")
    fees = row.get("fees_paid")
    tp = row.get("tp")
    sl = row.get("sl")
    bit_for_bit = (tp is not None and float(cp) == float(tp)) or (
        sl is not None and float(cp) == float(sl)
    )
    if pnl_r is not None and broker != "paper":
        # Fix 6.B (plan: post-XRP-close): trust pnl_r as the
        # authoritative broker-truth signal. The pre-fix path also
        # required `closed_price != tp/sl` bit-for-bit, but LIMIT
        # orders fill at exactly the limit price by design (a LIMIT
        # TP at 1.0586 fills at 1.0586). Bit-for-bit equality on a
        # populated pnl_r is normal LIMIT-fill broker truth, not a
        # synthetic settler signature. The "synthetic-live-bug"
        # bucket is reserved for the no-pnl_r case below — that's
        # the only path where bit-for-bit equality genuinely
        # implies the synthetic settler beat the broker callback.
        return "broker-truth-no-fee" if fees is None else "broker-truth"
    if broker != "paper" and bit_for_bit:
        return "synthetic-live-bug"
    return "synthetic-paper"


def _realised_r(row: dict) -> float | None:
    """R-multiple using the journal's stored entry/sl/closed_price.

    Mirrors Order.realised_pnl_R exactly so we measure the same number
    the live account ledger is booking via Account.book_close.
    Returns None for OPEN / REJECTED rows.
    """
    if row.get("entry") not in ("BUY", "SELL"):
        return None
    cp = row.get("closed_price")
    if cp is None:
        return None
    risk = abs(float(row["price"]) - float(row["sl"]))
    if risk == 0:
        return 0.0
    if row["entry"] == "BUY":
        return (float(cp) - float(row["price"])) / risk
    return (float(row["price"]) - float(cp)) / risk


def _rejection_reason(row: dict) -> str | None:
    """Extract the cap reason from a REJECTED sentinel like
    'REJECTED (max_open_positions (1) reached ...)'. Returns the leading
    cap name, or None if this row is not a rejection."""
    entry = row.get("entry") or ""
    if not entry.startswith("REJECTED"):
        return None
    if "(" not in entry:
        return "unknown"
    inside = entry.split("(", 1)[1]
    return inside.split(" ")[0].rstrip(")") or "unknown"


def _utc_day(ts: str) -> str:
    try:
        return ts.split("T", 1)[0]
    except Exception:
        return "?"


def build_report(rows: list[dict], *, bps_tol: float = 1.0) -> dict:
    """Compute the full diagnostic report dict."""
    real = [r for r in rows if r.get("entry") in ("BUY", "SELL")]
    rejected = [r for r in rows if (r.get("entry") or "").startswith("REJECTED")]

    by_outcome = Counter(r.get("outcome", "?") for r in real)
    closed = [r for r in real if r.get("outcome") in ("WIN", "LOSS", "BE", "CLOSED")]
    open_now = [r for r in real if r.get("outcome") == "OPEN"]

    # Close-reason classification + R distribution
    close_reasons = Counter(_classify_close(r, bps_tol) for r in closed)
    # Fix 2.J: truth-source classification (orthogonal to close-leg).
    truth_classes = Counter(_classify_truth(r) for r in closed)
    rs = [_realised_r(r) for r in closed]
    rs = [x for x in rs if x is not None]

    # R buckets per close reason (so we can see that BE rows really are 0R)
    r_by_reason: dict[str, list[float]] = defaultdict(list)
    for r in closed:
        reason = _classify_close(r, bps_tol)
        rr = _realised_r(r)
        if rr is not None:
            r_by_reason[reason].append(rr)

    # Per-pair view
    per_pair: dict[str, dict] = {}
    pairs = sorted({r["pair"] for r in real})
    for pair in pairs:
        pair_real = [r for r in real if r["pair"] == pair]
        pair_closed = [r for r in pair_real if r.get("outcome") in ("WIN", "LOSS", "BE", "CLOSED")]
        pair_rs = [x for x in (_realised_r(r) for r in pair_closed) if x is not None]
        pair_reasons = Counter(_classify_close(r, bps_tol) for r in pair_closed)
        pair_dirs = Counter(r["entry"] for r in pair_real)
        per_pair[pair] = {
            "fills": len(pair_real),
            "buy_fills": pair_dirs.get("BUY", 0),
            "sell_fills": pair_dirs.get("SELL", 0),
            "closed": len(pair_closed),
            "wins": sum(1 for r in pair_closed if r.get("outcome") == "WIN"),
            "losses": sum(1 for r in pair_closed if r.get("outcome") == "LOSS"),
            "be": sum(1 for r in pair_closed if r.get("outcome") == "BE"),
            "open": sum(1 for r in pair_real if r.get("outcome") == "OPEN"),
            "close_reasons": dict(pair_reasons),
            "r_mean": mean(pair_rs) if pair_rs else None,
            "r_median": median(pair_rs) if pair_rs else None,
            "r_sum": sum(pair_rs) if pair_rs else 0.0,
        }

    # Per-pair-per-day SL-hit rate (the headline number)
    per_pair_day: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(Counter))
    for r in closed:
        per_pair_day[r["pair"]][_utc_day(r.get("closed_ts") or r.get("ts", ""))][_classify_close(r, bps_tol)] += 1
        per_pair_day[r["pair"]][_utc_day(r.get("closed_ts") or r.get("ts", ""))]["_total"] += 1

    # Rejection breakdown
    rejection_reasons = Counter(_rejection_reason(r) or "unknown" for r in rejected)

    # Implied USDT P&L at both risk levels (starting balance 10k per
    # scanner.py:229; not exchange truth, but matches Account ledger).
    starting_balance = 10_000.0
    risk_pct_live = 0.0005
    risk_pct = 0.005
    total_r = sum(rs)
    usdt_at_live = starting_balance * risk_pct_live * total_r
    usdt_at_legacy = starting_balance * risk_pct * total_r

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "journal_path": str(JOURNAL_PATH),
        "bps_tol": bps_tol,
        "counters": {
            "total_rows": len(rows),
            "real_fills": len(real),
            "rejected": len(rejected),
            "by_outcome": dict(by_outcome),
            "by_direction": dict(Counter(r["entry"] for r in real)),
        },
        "rejection_reasons": dict(rejection_reasons),
        "close_reasons": dict(close_reasons),
        # Fix 2.J: orthogonal truth-source classifier. The `acceptance`
        # field is the one-line Phase 3 Layer 2 gate — true iff at
        # least one broker-truth row exists AND zero synthetic-live-bug
        # rows. Suitable for `jq '.acceptance'` in a smoke-test CI step.
        "truth_classes": dict(truth_classes),
        "acceptance": bool(
            (truth_classes.get("broker-truth", 0) + truth_classes.get("broker-truth-no-fee", 0)) > 0
            and truth_classes.get("synthetic-live-bug", 0) == 0
        ),
        "r_by_close_reason": {
            k: {
                "n": len(v),
                "mean": mean(v) if v else None,
                "median": median(v) if v else None,
                "sum": sum(v),
                "min": min(v) if v else None,
                "max": max(v) if v else None,
            }
            for k, v in r_by_reason.items()
        },
        "r_overall": {
            "n": len(rs),
            "mean": mean(rs) if rs else None,
            "median": median(rs) if rs else None,
            "sum": sum(rs),
        },
        "implied_usdt_pnl": {
            "starting_balance": starting_balance,
            "at_risk_pct_live_0_0005": round(usdt_at_live, 4),
            "at_risk_pct_0_005": round(usdt_at_legacy, 4),
            "ratio": "10x — the silent-oversizing if SHADOW_MODE=false",
        },
        "per_pair": per_pair,
        "per_pair_day": {p: {d: dict(c) for d, c in days.items()} for p, days in per_pair_day.items()},
        "open_now": [
            {
                "pair": r["pair"],
                "side": r["entry"],
                "ts": r.get("ts"),
                "price": r.get("price"),
                "sl": r.get("sl"),
                "tp": r.get("tp"),
            }
            for r in open_now
        ],
    }


def _print_human(report: dict) -> None:
    c = report["counters"]
    print("=" * 72)
    print(f"Live P&L diagnostic — {report['generated_at']}")
    print(f"Journal: {report['journal_path']}")
    print(f"bps tolerance for close-reason classifier: {report['bps_tol']}")
    print("=" * 72)
    print()
    print("--- Top-line counters ---")
    print(f"  rows total       : {c['total_rows']}")
    print(f"  real fills       : {c['real_fills']}    "
          f"(buys={c['by_direction'].get('BUY',0)} sells={c['by_direction'].get('SELL',0)})")
    print(f"  rejected         : {c['rejected']}")
    print(f"  outcomes         : {c['by_outcome']}")
    print()

    print("--- Rejection reasons ---")
    for k, v in sorted(report["rejection_reasons"].items(), key=lambda x: -x[1]):
        print(f"  {k:32s}: {v}")
    print()

    print("--- Close-reason classification (closed_price matches which leg?) ---")
    total_classified = sum(report["close_reasons"].values()) or 1
    for k, v in sorted(report["close_reasons"].items(), key=lambda x: -x[1]):
        pct = 100.0 * v / total_classified
        print(f"  {k:16s}: {v:4d}   {pct:5.1f}%")
    print()

    print("--- Truth-source classification (Fix 2.J — Phase 3 Layer 2 gate) ---")
    truth_total = sum(report["truth_classes"].values()) or 1
    # Friendly order: broker-truth first, then degraded/synthetic.
    order = [
        "broker-truth",
        "broker-truth-no-fee",
        "synthetic-paper",
        "synthetic-live-bug",
        "partial",
    ]
    seen_keys = set(report["truth_classes"].keys())
    for k in order + sorted(seen_keys - set(order)):
        v = report["truth_classes"].get(k, 0)
        if v == 0:
            continue
        pct = 100.0 * v / truth_total
        warn = " ⚠ FIX-2.B REGRESSION" if k == "synthetic-live-bug" else ""
        print(f"  {k:24s}: {v:4d}   {pct:5.1f}%{warn}")
    acc = report.get("acceptance")
    if acc is True:
        print(f"  acceptance gate         : PASS")
    elif report["truth_classes"]:
        print(f"  acceptance gate         : FAIL  "
              f"(need ≥1 broker-truth row AND 0 synthetic-live-bug)")
    else:
        print(f"  acceptance gate         : N/A   (no closed rows yet)")
    print()

    print("--- R-multiple by close reason ---")
    for k, stats in sorted(report["r_by_close_reason"].items()):
        mean_str = f"{stats['mean']:+.3f}" if stats['mean'] is not None else "  n/a"
        med_str = f"{stats['median']:+.3f}" if stats['median'] is not None else "  n/a"
        print(f"  {k:16s}: n={stats['n']:4d}  mean={mean_str}  median={med_str}  sum={stats['sum']:+.3f}")
    print()

    overall = report["r_overall"]
    if overall["n"]:
        print(f"--- Overall R: n={overall['n']}  mean={overall['mean']:+.3f}  "
              f"median={overall['median']:+.3f}  sum={overall['sum']:+.3f}")
    else:
        print("--- Overall R: no closed trades to score")
    print()

    pnl = report["implied_usdt_pnl"]
    print("--- Implied USDT P&L (Account ledger view, starting balance $10k) ---")
    print(f"  at RISK_PCT_LIVE=0.0005 (intended)   : ${pnl['at_risk_pct_live_0_0005']:+.2f}")
    print(f"  at RISK_PCT=0.005      (actually used): ${pnl['at_risk_pct_0_005']:+.2f}")
    print(f"  {pnl['ratio']}")
    print()

    print("--- Per-pair breakdown ---")
    hdr = f"  {'pair':22s} {'fills':>6s} {'BUY':>5s} {'SELL':>5s} {'closed':>7s} {'W':>4s} {'L':>4s} {'BE':>4s} {'open':>5s} {'R_mean':>8s} {'R_sum':>8s}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for pair, p in report["per_pair"].items():
        r_mean = f"{p['r_mean']:+.3f}" if p["r_mean"] is not None else "    n/a"
        r_sum = f"{p['r_sum']:+.3f}"
        print(f"  {pair:22s} {p['fills']:>6d} {p['buy_fills']:>5d} {p['sell_fills']:>5d} "
              f"{p['closed']:>7d} {p['wins']:>4d} {p['losses']:>4d} {p['be']:>4d} "
              f"{p['open']:>5d} {r_mean:>8s} {r_sum:>8s}")
        # close-reason breakdown
        if p["close_reasons"]:
            print(f"    reasons: {p['close_reasons']}")
    print()

    if report["open_now"]:
        print(f"--- {len(report['open_now'])} positions still OPEN in journal ---")
        for o in report["open_now"]:
            print(f"  {o['ts']}  {o['pair']:22s} {o['side']:4s}  price={o['price']}  sl={o['sl']}  tp={o['tp']}")
        print()


def build_smoke_gate(
    rows: list[dict], pairs: list[str]
) -> dict:
    """Fix 9.G — Phase 9 smoke gate.

    Each pair passes the gate when it has ≥ 1 closed row with broker
    truth (pnl_r populated AND broker != 'paper'). The aggregate gate
    passes when EVERY configured pair has crossed that bar.

    Returns:
      {
        "pairs_passed": [...],
        "pairs_pending": [...],
        "smoke_gate_pass": bool,
        "per_pair": {pair: {"truth_count": int, "first_close_ts": str|None}},
      }

    Used by ops to decide when the 5-pair smoke gate is operationally
    proven. Mirror-to-mainnet promotion (Tier
    5) waits for `smoke_gate_pass: true`.
    """
    per_pair: dict[str, dict] = {p: {"truth_count": 0, "first_close_ts": None} for p in pairs}
    for r in rows:
        pair = r.get("pair")
        if pair not in per_pair:
            continue
        if _classify_truth(r) not in ("broker-truth", "broker-truth-no-fee"):
            continue
        per_pair[pair]["truth_count"] += 1
        ts = r.get("closed_ts") or r.get("ts")
        if ts and (
            per_pair[pair]["first_close_ts"] is None
            or ts < per_pair[pair]["first_close_ts"]
        ):
            per_pair[pair]["first_close_ts"] = ts

    passed = sorted(p for p, s in per_pair.items() if s["truth_count"] > 0)
    pending = sorted(p for p, s in per_pair.items() if s["truth_count"] == 0)
    return {
        "pairs_passed": passed,
        "pairs_pending": pending,
        "smoke_gate_pass": len(pending) == 0,
        "per_pair": per_pair,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only live P&L diagnostic.")
    ap.add_argument("--journal", default=str(JOURNAL_PATH),
                    help=f"Path to signals.json (default: {JOURNAL_PATH})")
    ap.add_argument("--bps-tol", type=float, default=1.0,
                    help="Tolerance (bps) for matching closed_price to a bracket leg.")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of human text.")
    ap.add_argument(
        "--smoke-gate",
        action="store_true",
        help="Fix 9.G: emit Phase 9 5-pair smoke gate status. Exits with "
             "0 when every configured pair has at least one broker-truth "
             "close, 1 when any pair is still pending.",
    )
    args = ap.parse_args()

    journal = Path(args.journal)
    if not journal.exists():
        print(f"Journal not found: {journal}", file=sys.stderr)
        return 2

    with open(journal) as f:
        rows = json.load(f)

    if args.smoke_gate:
        from ictbot.settings import PAIRS

        gate = build_smoke_gate(rows, list(PAIRS))
        if args.json:
            json.dump(gate, sys.stdout, indent=2, default=str)
            sys.stdout.write("\n")
        else:
            verdict = "PASS" if gate["smoke_gate_pass"] else "PENDING"
            n_pairs = len(gate["per_pair"])
            print(f"{n_pairs}-pair smoke gate: {verdict}")
            print(f"  passed  ({len(gate['pairs_passed'])}): {gate['pairs_passed']}")
            print(f"  pending ({len(gate['pairs_pending'])}): {gate['pairs_pending']}")
            for pair, stat in gate["per_pair"].items():
                first = stat["first_close_ts"] or "—"
                print(f"    {pair:<22} truth={stat['truth_count']}  first={first}")
        return 0 if gate["smoke_gate_pass"] else 1

    report = build_report(rows, bps_tol=args.bps_tol)
    report["journal_path"] = str(journal)  # honour --journal in human output
    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
