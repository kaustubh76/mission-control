"""
Phase 16 — Session-bucketed daily trade report.

The user's ICT prior: strategy edge concentrates in the **London**
and **New York** killzones (when retail + institutional liquidity
overlap). Off-session signals should approach zero expectancy. This
script tests that prior on the live journal and writes a deliberate
markdown report so the operator can read it next morning over coffee.

The report writes to `data/reports/session_<UTC-date>.md` and contains:

  * Top-line buckets: IN_SESSION vs OFF_SESSION (mean R, sum R, win
    rate, t-stat vs 0)
  * Welch's t comparison between buckets (does in-session beat
    off-session statistically?)
  * Per-pair × bucket breakdown
  * Trade-by-trade log (broker-truth only)
  * Cap-rejection breakdown by bucket
  * A clear verdict line per Phase 14.D thresholds:
      n < 10                → insufficient data
      n < 30                → no signal yet
      |t| ≤ 2               → no edge (≈ break-even)
      t > 2,  mean > 0      → REAL EDGE
      t < -2, mean < 0      → NEGATIVE EDGE

USAGE:
  python3 scripts/session_report.py                       # today UTC
  python3 scripts/session_report.py --date 2026-06-07
  python3 scripts/session_report.py --no-write            # stdout only
  python3 scripts/session_report.py --out data/custom.md  # custom path

Or:
  make session_report
  make session_report ARGS="--date 2026-06-07"
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date as date_cls
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev

from ictbot.runtime.sessions import get_sessions

REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = REPO_ROOT / "data" / "journal" / "signals.json"
REPORTS_DIR = REPO_ROOT / "data" / "reports"

IN_SESSION_NAMES = {"LONDON", "NEW YORK"}


# ---- Bucketing -----------------------------------------------------------


def _row_session(row: dict) -> str:
    """Return the session label for a row. Prefers the stored field
    (Fix 16.A persists `active_session`); falls back to reconstructing
    from `ts` for legacy rows."""
    stored = row.get("session")
    if isinstance(stored, str) and stored:
        return stored
    ts = row.get("ts")
    if not ts:
        return "UNKNOWN"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return "UNKNOWN"
    s = get_sessions(at=dt)
    return s.get("active_session") or "UNKNOWN"


def _is_in_session(label: str) -> bool:
    """A label counts as IN_SESSION when London or NY is OPEN."""
    label = (label or "").upper()
    return any(name in label for name in IN_SESSION_NAMES)


def _in_date(row: dict, target: date_cls) -> bool:
    ts = row.get("ts")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.astimezone(timezone.utc).date() == target


# ---- Stats (mirrors scripts/edge_check.py) -------------------------------


def _t_stat(sample: list[float], mu0: float = 0.0) -> float | None:
    n = len(sample)
    if n < 2:
        return None
    m = mean(sample)
    s = stdev(sample)
    if s == 0:
        return None
    return (m - mu0) / (s / math.sqrt(n))


def _normal_p_two_sided(t: float | None) -> float | None:
    if t is None:
        return None
    return math.erfc(abs(t) / math.sqrt(2))


def _welch_t(a: list[float], b: list[float]) -> float | None:
    """Welch's two-sample t-test for unequal variances. The unequal-N
    bucket comparison the user wants ('in beats off?')."""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = mean(a), mean(b)
    sa, sb = stdev(a), stdev(b)
    na, nb = len(a), len(b)
    denom_sq = sa * sa / na + sb * sb / nb
    if denom_sq <= 0:
        return None
    return (ma - mb) / math.sqrt(denom_sq)


def _verdict(n: int, m: float, t: float | None, min_n: int = 30) -> str:
    if n < min_n // 3:
        return "insufficient data — n < 10"
    if n < min_n:
        return "no signal yet — n < 30"
    if t is None:
        return "insufficient data — zero variance"
    if t > 2.0 and m > 0:
        return "**REAL EDGE** — mean R > 0 significant"
    if t < -2.0 and m < 0:
        return "**NEGATIVE EDGE** — strategy losing"
    return "no edge — strategy near break-even"


# ---- Aggregator ----------------------------------------------------------


def _classify_broker_truth(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        outcome = r.get("outcome")
        if outcome not in ("WIN", "LOSS", "BE", "CLOSED"):
            continue
        broker = r.get("broker", "paper")
        if broker in (None, "paper"):
            continue
        if r.get("pnl_r") is None:
            continue
        out.append(r)
    return out


def _classify_rejected(rows: list[dict]) -> list[dict]:
    return [
        r for r in rows
        if (r.get("entry") or "").upper().startswith("REJECTED")
    ]


def _bucket_stats(rows: list[dict]) -> dict:
    """Stats for a single bucket of broker-truth rows."""
    sample = [float(r["pnl_r"]) for r in rows]
    n = len(sample)
    m = mean(sample) if sample else 0.0
    s = stdev(sample) if n > 1 else 0.0
    wins = sum(1 for x in sample if x > 0)
    losses = sum(1 for x in sample if x < 0)
    wr = (wins / n * 100) if n > 0 else 0.0
    t = _t_stat(sample, 0.0)
    p = _normal_p_two_sided(t) if n >= 30 else None
    fees = sum(float(r.get("fees_paid") or 0) for r in rows)
    return {
        "n": n, "mean_r": m, "std_r": s, "sum_r": sum(sample),
        "wins": wins, "losses": losses, "win_rate_pct": wr,
        "t_vs_zero": t, "p_vs_zero": p, "fees_usdt": fees,
        "sample": sample,
        "rows": rows,
    }


def build_report(rows: list[dict], target: date_cls) -> dict:
    """Aggregate everything the markdown writer needs."""
    day_rows = [r for r in rows if _in_date(r, target)]
    truth = _classify_broker_truth(day_rows)
    rejected = _classify_rejected(day_rows)

    in_truth = [r for r in truth if _is_in_session(_row_session(r))]
    off_truth = [r for r in truth if not _is_in_session(_row_session(r))]

    in_rej = [r for r in rejected if _is_in_session(_row_session(r))]
    off_rej = [r for r in rejected if not _is_in_session(_row_session(r))]

    buckets = {
        "IN_SESSION": _bucket_stats(in_truth),
        "OFF_SESSION": _bucket_stats(off_truth),
        "OVERALL": _bucket_stats(truth),
    }

    # In-vs-off Welch's t
    welch = _welch_t(buckets["IN_SESSION"]["sample"], buckets["OFF_SESSION"]["sample"])

    # Per-pair × bucket
    per_pair = {}
    for r in truth:
        pair = r.get("pair") or "?"
        bucket = "IN_SESSION" if _is_in_session(_row_session(r)) else "OFF_SESSION"
        per_pair.setdefault(pair, {"IN_SESSION": [], "OFF_SESSION": []})
        per_pair[pair][bucket].append(r)

    per_pair_stats = {}
    for pair, bdict in per_pair.items():
        per_pair_stats[pair] = {
            "IN_SESSION": _bucket_stats(bdict["IN_SESSION"]),
            "OFF_SESSION": _bucket_stats(bdict["OFF_SESSION"]),
        }

    # Rejection breakdown
    def _rej_summary(rows_):
        total = len(rows_)
        head_counts = {}
        for r in rows_:
            head = (r.get("entry") or "").split("(", 1)
            if len(head) > 1:
                k = head[1].split(")")[0].split(" ")[0].strip()
            else:
                k = "unknown"
            head_counts[k] = head_counts.get(k, 0) + 1
        return {"total": total, "by_reason": head_counts}

    return {
        "date": target.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "buckets": buckets,
        "welch_t_in_vs_off": welch,
        "per_pair_stats": per_pair_stats,
        "rejections": {
            "IN_SESSION": _rej_summary(in_rej),
            "OFF_SESSION": _rej_summary(off_rej),
        },
    }


# ---- Markdown writer ------------------------------------------------------


def _fmt_r(x):
    return f"{x:+.3f}" if isinstance(x, (int, float)) else "n/a"


def _fmt_pct(x):
    return f"{x:.1f}%" if isinstance(x, (int, float)) else "n/a"


def _fmt_p(p):
    if p is None:
        return "n/a"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _bucket_row(name: str, b: dict) -> str:
    return (
        f"| {name} | {b['n']} | {_fmt_r(b['mean_r'])} | {_fmt_r(b['sum_r'])} | "
        f"{_fmt_pct(b['win_rate_pct'])} | "
        f"{_fmt_r(b['t_vs_zero']) if b['t_vs_zero'] is not None else 'n/a'} | "
        f"{_fmt_p(b['p_vs_zero'])} |"
    )


def _render_markdown(report: dict) -> str:
    parts = []
    parts.append(f"# Session-bucketed trade report — {report['date']} UTC")
    parts.append("")
    parts.append(f"*Generated {report['generated_at']}. "
                 f"Journal: `data/journal/signals.json`.*")
    parts.append("")
    parts.append("## Hypothesis under test")
    parts.append("")
    parts.append("ICT prior: strategy edge concentrates in **London** and "
                 "**New York** killzones. Off-session signals should "
                 "approach zero expectancy. This report tests that prior.")
    parts.append("")

    # Top-line table
    parts.append("## Top-line by bucket")
    parts.append("")
    parts.append("| Bucket | N | Mean R | Sum R | Win rate | t vs 0 | p |")
    parts.append("|---|---|---|---|---|---|---|")
    parts.append(_bucket_row("IN_SESSION (London + NY)", report["buckets"]["IN_SESSION"]))
    parts.append(_bucket_row("OFF_SESSION (Tokyo + off-hours)", report["buckets"]["OFF_SESSION"]))
    parts.append(_bucket_row("**OVERALL**", report["buckets"]["OVERALL"]))
    parts.append("")

    # In-vs-off comparison
    parts.append("## In-session vs off-session")
    parts.append("")
    in_b = report["buckets"]["IN_SESSION"]
    off_b = report["buckets"]["OFF_SESSION"]
    delta = in_b["mean_r"] - off_b["mean_r"]
    parts.append(f"- mean delta (IN − OFF): {delta:+.3f}R")
    welch = report["welch_t_in_vs_off"]
    if welch is None:
        parts.append("- Welch's t: insufficient samples in one bucket")
        parts.append("- verdict: **pending** (need ≥ 2 closes per bucket)")
    else:
        parts.append(f"- Welch's t (in vs off): {welch:+.2f} "
                     f"(n_in={in_b['n']}, n_off={off_b['n']})")
        if abs(welch) <= 2.0:
            verdict = "**inconclusive** — buckets not statistically different yet"
        elif welch > 2.0:
            verdict = "**IN_SESSION edge LIKELY** — killzone hypothesis supported"
        else:
            verdict = "**OFF_SESSION winning** — killzone hypothesis CONTRADICTED"
        parts.append(f"- verdict: {verdict}")
    parts.append("")
    parts.append("Per-bucket vs 0-expectancy verdict:")
    parts.append(f"- IN_SESSION:  {_verdict(in_b['n'], in_b['mean_r'], in_b['t_vs_zero'])}")
    parts.append(f"- OFF_SESSION: {_verdict(off_b['n'], off_b['mean_r'], off_b['t_vs_zero'])}")
    parts.append("")

    # Per-pair × bucket
    parts.append("## Per-pair × bucket")
    parts.append("")
    if not report["per_pair_stats"]:
        parts.append("*(no broker-truth closes for this date)*")
    else:
        for bucket in ("IN_SESSION", "OFF_SESSION"):
            parts.append(f"### {bucket}")
            parts.append("")
            parts.append("| Pair | N | Mean R | Sum R | Win rate |")
            parts.append("|---|---|---|---|---|")
            for pair, stats in sorted(report["per_pair_stats"].items()):
                b = stats[bucket]
                parts.append(
                    f"| {pair} | {b['n']} | {_fmt_r(b['mean_r'])} | "
                    f"{_fmt_r(b['sum_r'])} | {_fmt_pct(b['win_rate_pct'])} |"
                )
            parts.append("")

    # Trade-by-trade
    parts.append("## Trade-by-trade (broker-truth only)")
    parts.append("")
    for bucket in ("IN_SESSION", "OFF_SESSION"):
        parts.append(f"### {bucket}")
        parts.append("")
        rows = report["buckets"][bucket]["rows"]
        if not rows:
            parts.append("*(none today)*")
            parts.append("")
            continue
        for r in sorted(rows, key=lambda x: x.get("closed_ts") or x.get("ts") or ""):
            closed_ts = (r.get("closed_ts") or "").split(".")[0]
            session = _row_session(r)
            reason = r.get("close_reason") or "—"
            r_val = r.get("pnl_r") or 0.0
            parts.append(
                f"- `{closed_ts}` {r.get('pair', '?'):<22} "
                f"{r.get('entry', '?'):<5} {r.get('outcome', '?'):<5} "
                f"reason={reason}  R={r_val:+.3f}  session={session}"
            )
        parts.append("")

    # Rejection breakdown
    parts.append("## Cap rejections by bucket")
    parts.append("")
    for bucket in ("IN_SESSION", "OFF_SESSION"):
        rej = report["rejections"][bucket]
        parts.append(f"### {bucket}: {rej['total']} rejected signals")
        if rej["by_reason"]:
            for k, v in sorted(rej["by_reason"].items(), key=lambda x: -x[1]):
                parts.append(f"- {k}: {v}")
        parts.append("")

    # Verdict footer
    parts.append("---")
    parts.append("## Decision-quality note")
    parts.append("")
    total_n = report["buckets"]["OVERALL"]["n"]
    if total_n < 10:
        parts.append("**Insufficient data** to make a project-level call. "
                     "Need ≥ 30 broker-truth closes; currently have "
                     f"{total_n}. Keep observing.")
    elif total_n < 30:
        parts.append(f"Sample is still small ({total_n} broker-truth "
                     "closes). Continue observation; revisit at N≥30.")
    else:
        in_v = _verdict(in_b["n"], in_b["mean_r"], in_b["t_vs_zero"])
        off_v = _verdict(off_b["n"], off_b["mean_r"], off_b["t_vs_zero"])
        parts.append(f"At N={total_n} broker-truth closes:")
        parts.append(f"- IN_SESSION:  {in_v}")
        parts.append(f"- OFF_SESSION: {off_v}")
        if "REAL EDGE" in in_v and ("REAL EDGE" not in off_v):
            parts.append("")
            parts.append("**Recommendation**: KEEP project + flip "
                         "`KILLZONE_REQUIRED=true` to gate off off-session "
                         "signals.")
        elif "REAL EDGE" in in_v and "REAL EDGE" in off_v:
            parts.append("")
            parts.append("**Recommendation**: KEEP project + KEEP both "
                         "sessions trading (edge wider than expected).")
        elif "NEGATIVE EDGE" in in_v or "NEGATIVE EDGE" in off_v:
            parts.append("")
            parts.append("**Recommendation**: DROP project as configured. "
                         "WFO was curve-fit OR market regime changed.")
        else:
            parts.append("")
            parts.append("**Recommendation**: continue observing — no "
                         "decisive signal yet.")
    parts.append("")
    return "\n".join(parts)


# ---- Main ---------------------------------------------------------------


def _read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--date", default=None,
                    help="Target UTC date (YYYY-MM-DD); default = today")
    ap.add_argument("--journal", default=str(JOURNAL_PATH),
                    help=f"Journal path (default: {JOURNAL_PATH})")
    ap.add_argument("--out", default=None,
                    help=f"Output MD path (default: {REPORTS_DIR}/session_<date>.md)")
    ap.add_argument("--no-write", action="store_true",
                    help="Skip writing the file; print to stdout only.")
    args = ap.parse_args()

    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid --date: {args.date} (expected YYYY-MM-DD)",
                  file=sys.stderr)
            return 2
    else:
        target = datetime.now(timezone.utc).date()

    rows = _read_journal(Path(args.journal))
    report = build_report(rows, target)
    md = _render_markdown(report)

    if args.no_write:
        sys.stdout.write(md)
        return 0

    out_path = Path(args.out) if args.out else REPORTS_DIR / f"session_{target.isoformat()}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    print(f"Report written to {out_path}")
    # Echo top-line buckets so the operator sees the answer without
    # opening the file.
    o = report["buckets"]["OVERALL"]
    i = report["buckets"]["IN_SESSION"]
    f = report["buckets"]["OFF_SESSION"]
    print(f"  IN_SESSION:  n={i['n']:>3} mean R={i['mean_r']:+.3f}")
    print(f"  OFF_SESSION: n={f['n']:>3} mean R={f['mean_r']:+.3f}")
    print(f"  OVERALL:     n={o['n']:>3} mean R={o['mean_r']:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
