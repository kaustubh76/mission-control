"""
Fix 5.F (plan: Phase 5 Tier 3) — Phase 3 Layer 2 acceptance closure.

Closes the original acceptance criterion: "diagnostic's implied USDT
P&L matches the testnet wallet's fetch_balance change within fee
precision". Fix 2.J shipped the journal side via the broker-truth
classifier; this script ships the wallet side.

How parity is computed:
  1. Read journal rows closed since a `--since` cutoff (default = today UTC).
     Filter to broker=binance-live, outcome in {WIN, LOSS, BE, CLOSED}.
  2. Sum the realised USDT impact: pnl_r × RISK_PCT_LIVE × starting_balance.
     Sum fees_paid for the actual cost side.
  3. Fetch the Binance USDT balance via ccxt.
  4. Compare to a baseline stored at data/wallet_baseline_usdt.txt.
     Write the baseline on first run (no-op compare, exit 0).
     The operator can rebase explicitly via `--rebase`.
  5. parity_ok iff |wallet_delta - journal_usdt| <= tolerance.

Exit codes:
  0 — parity OK (or baseline just initialised)
  1 — drift exceeds tolerance
  2 — infra error (no journal, no API key, ccxt failure, etc.)

Usage:
  python scripts/verify_wallet_parity.py
  python scripts/verify_wallet_parity.py --since 2026-06-06
  python scripts/verify_wallet_parity.py --rebase
  python scripts/verify_wallet_parity.py --tolerance 1.0
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = REPO_ROOT / "data" / "journal" / "signals.json"
BASELINE_PATH = REPO_ROOT / "data" / "wallet_baseline_usdt.txt"
DEFAULT_TOLERANCE_USDT = 0.50


def _read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _utc_today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def compute_journal_pnl(
    rows: list[dict],
    *,
    since_iso: str,
    risk_pct_live: float,
    starting_balance: float,
) -> dict:
    """Pure: sum realised USDT impact from journal rows since cutoff.

    Returns a dict with `journal_usdt`, `fees_paid_total`, and a
    per-pair breakdown. Only counts broker=binance-live closed rows
    with `pnl_r` populated (broker-truth path).
    """
    closed_states = {"WIN", "LOSS", "BE", "CLOSED"}
    relevant = []
    for r in rows:
        if r.get("broker") != "binance-live":
            continue
        if r.get("outcome") not in closed_states:
            continue
        if r.get("pnl_r") is None:
            continue
        ts = r.get("closed_ts") or r.get("ts") or ""
        if ts < since_iso:
            continue
        relevant.append(r)

    per_pair: dict[str, dict] = {}
    total_usdt = 0.0
    total_fees = 0.0
    for r in relevant:
        pair = r.get("pair", "?")
        pnl_r = float(r["pnl_r"])
        usdt = pnl_r * risk_pct_live * starting_balance
        fees = float(r.get("fees_paid") or 0.0)
        d = per_pair.setdefault(pair, {"n": 0, "pnl_usdt": 0.0, "fees": 0.0})
        d["n"] += 1
        d["pnl_usdt"] += usdt
        d["fees"] += fees
        total_usdt += usdt
        total_fees += fees
    return {
        "rows_counted": len(relevant),
        "journal_usdt": round(total_usdt, 4),
        "fees_paid_total": round(total_fees, 4),
        "per_pair": {p: {k: round(v, 4) if isinstance(v, float) else v
                         for k, v in d.items()} for p, d in per_pair.items()},
    }


def compute_parity(
    *,
    journal_usdt: float,
    wallet_delta: float,
    tolerance: float,
) -> dict:
    """Pure: compare expected (journal) vs observed (wallet)."""
    drift = wallet_delta - journal_usdt
    return {
        "journal_usdt": round(journal_usdt, 4),
        "wallet_delta_usdt": round(wallet_delta, 4),
        "drift_usdt": round(drift, 4),
        "tolerance_usdt": round(tolerance, 4),
        "parity_ok": bool(abs(drift) <= tolerance),
    }


def _read_baseline(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        return float(path.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_baseline(path: Path, balance: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{balance:.6f}\n")


def _fetch_wallet_usdt() -> float:
    """Pull current USDT balance via the broker. Isolated so tests can
    monkey-patch it. Returns -1.0 on failure (caller treats as infra
    error)."""
    from ictbot.exec.binance_live import BinanceLiveBroker
    from ictbot.settings import settings

    b = BinanceLiveBroker(
        allowed_pairs=set(),  # we only need balance, no pair gating
        testnet=settings.binance_testnet,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    return float(b.equity())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--journal", default=str(JOURNAL_PATH))
    ap.add_argument("--baseline", default=str(BASELINE_PATH))
    ap.add_argument("--since", default=_utc_today_iso(),
                    help="ISO date (UTC); rows closed before this are excluded")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_USDT,
                    help="Max allowed |drift| in USDT")
    ap.add_argument("--rebase", action="store_true",
                    help="Overwrite the baseline with the current wallet balance")
    ap.add_argument("--starting-balance", type=float, default=10_000.0,
                    help="Account ledger starting balance (default 10k)")
    ap.add_argument("--risk-pct-live", type=float, default=None,
                    help="Override settings.risk_pct_live (default = settings)")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON instead of human text")
    args = ap.parse_args()

    journal_path = Path(args.journal)
    baseline_path = Path(args.baseline)
    if not journal_path.exists():
        print(f"Journal not found: {journal_path}", file=sys.stderr)
        return 2

    # Live wallet balance via the broker.
    try:
        wallet_usdt = _fetch_wallet_usdt()
    except Exception as exc:  # noqa: BLE001
        print(f"fetch_balance failed: {exc}", file=sys.stderr)
        return 2
    if wallet_usdt <= 0:
        print(f"wallet balance non-positive ({wallet_usdt}); abort", file=sys.stderr)
        return 2

    if args.rebase:
        _write_baseline(baseline_path, wallet_usdt)
        print(f"baseline rebased to {wallet_usdt:.4f} USDT at {baseline_path}")
        return 0

    baseline = _read_baseline(baseline_path)
    if baseline is None:
        _write_baseline(baseline_path, wallet_usdt)
        msg = (f"baseline initialised at {wallet_usdt:.4f} USDT "
               f"({baseline_path}). Re-run after the first close.")
        if args.json:
            json.dump({"baseline_initialised": True,
                       "wallet_usdt": wallet_usdt}, sys.stdout)
            sys.stdout.write("\n")
        else:
            print(msg)
        return 0

    # Settings — risk_pct_live + starting_balance for the USDT projection.
    from ictbot.settings import settings as _s
    risk_pct_live = (
        args.risk_pct_live if args.risk_pct_live is not None
        else float(_s.risk_pct_live)
    )

    rows = _read_journal(journal_path)
    journal = compute_journal_pnl(
        rows,
        since_iso=args.since,
        risk_pct_live=risk_pct_live,
        starting_balance=args.starting_balance,
    )
    wallet_delta = wallet_usdt - baseline
    parity = compute_parity(
        journal_usdt=journal["journal_usdt"],
        wallet_delta=wallet_delta,
        tolerance=args.tolerance,
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "journal_path": str(journal_path),
        "baseline_path": str(baseline_path),
        "since": args.since,
        "risk_pct_live": risk_pct_live,
        "starting_balance": args.starting_balance,
        "wallet_now_usdt": round(wallet_usdt, 4),
        "wallet_baseline_usdt": round(baseline, 4),
        **journal,
        **parity,
    }

    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print("=" * 60)
        print(f"verify_wallet_parity — since {args.since}")
        print("=" * 60)
        print(f"  rows counted          : {report['rows_counted']}")
        print(f"  journal pnl (USDT)    : {report['journal_usdt']:+.4f}")
        print(f"  fees paid (USDT)      : {report['fees_paid_total']:.4f}")
        print(f"  wallet baseline       : {report['wallet_baseline_usdt']:.4f}")
        print(f"  wallet now            : {report['wallet_now_usdt']:.4f}")
        print(f"  wallet delta          : {report['wallet_delta_usdt']:+.4f}")
        print(f"  drift (wallet−journal): {report['drift_usdt']:+.4f}")
        print(f"  tolerance             : {report['tolerance_usdt']:.4f}")
        print(f"  parity OK             : {'YES' if report['parity_ok'] else 'NO'}")
        if report["per_pair"]:
            print()
            print("  per-pair contribution:")
            for pair, d in sorted(report["per_pair"].items()):
                print(f"    {pair:<22s} n={d['n']:<3d} "
                      f"pnl=${d['pnl_usdt']:+.4f}  fees=${d['fees']:.4f}")
    return 0 if report["parity_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
