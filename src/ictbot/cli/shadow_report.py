"""
Phase B — shadow report (legacy ictbot).

Compares the live and shadow legs of the ShadowRouter using whatever
data the local process has on hand:

  1. Live signals from data/journal/signals.json (always populated when
     the live broker placed an order).
  2. Prometheus metrics in-process (shadow_fill_slippage_bps,
     shadow_diverged_total) — only when prometheus_client is installed,
     because that's the only path that retains observed values.

USAGE:
  python -m ictbot.cli.shadow_report                # human-readable summary
  python -m ictbot.cli.shadow_report --telegram     # also push to TG
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from statistics import median

from ictbot.portfolio.journal import read_journal, score_journal


def _live_summary() -> str:
    """Per-pair WIN/LOSS/OPEN tally from the live journal."""
    entries = read_journal()
    if not entries:
        return "(no live journal entries yet)"
    by_pair: dict[str, list] = defaultdict(list)
    for e in entries:
        by_pair[e["pair"]].append(e)
    lines = ["LIVE journal — per-pair tally"]
    lines.append("-" * 60)
    for pair, rows in sorted(by_pair.items()):
        stats = score_journal(rows)
        wr = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "—"
        lines.append(
            f"  {pair:<20}  total={stats['total']:<3}  W/L/O="
            f"{stats['wins']}/{stats['losses']}/{stats['open']}  win-rate={wr}"
        )
    return "\n".join(lines)


def _by_broker_summary() -> str:
    """Fix 2.H (plan: live P&L clean-up): split the journal by `broker`
    field (added in Fix 2.A) so live and paper/shadow rows can be
    compared apples-to-apples per pair. Uses `pnl_r` written by
    `mark_closed_from_broker` when available; falls back to the legacy
    +rr/-1 estimate so historical paper rows still score.
    """
    entries = read_journal()
    if not entries:
        return "(no journal entries yet)"

    by_broker_pair: dict[tuple[str, str], list[float]] = defaultdict(list)
    closed_counts: dict[tuple[str, str], int] = defaultdict(int)
    for e in entries:
        if e.get("entry") not in ("BUY", "SELL"):
            continue
        outcome = e.get("outcome")
        if outcome not in ("WIN", "LOSS", "BE", "CLOSED"):
            continue
        broker = e.get("broker", "paper")
        pair = e.get("pair", "?")
        # Prefer the broker-stamped pnl_r (Fix 2.F); fall back to a
        # win/loss approximation from rr so pre-fix rows still tally.
        r = e.get("pnl_r")
        if r is None:
            rr = float(e.get("rr") or 0.0) or 0.0
            r = rr if outcome == "WIN" else (0.0 if outcome == "BE" else -1.0)
        by_broker_pair[(broker, pair)].append(float(r))
        closed_counts[(broker, pair)] += 1

    if not by_broker_pair:
        return "(no closed rows in journal yet)"

    # Reshape: pair → {broker: (n, mean_R, sum_R)}
    pairs = sorted({pair for (_, pair) in by_broker_pair.keys()})
    brokers = sorted({b for (b, _) in by_broker_pair.keys()})

    lines = ["JOURNAL — per-pair R by broker"]
    lines.append("-" * 60)
    header = f"  {'pair':<20s}"
    for b in brokers:
        header += f"  {b:>16s} (n)"
    lines.append(header)
    for pair in pairs:
        row = f"  {pair:<20s}"
        for b in brokers:
            rs = by_broker_pair.get((b, pair), [])
            if rs:
                mean_r = sum(rs) / len(rs)
                row += f"  {mean_r:+.3f}R ({len(rs):>3d})"
            else:
                row += f"  {'-':>10s}      "
        lines.append(row)

    # Pairwise delta if both live and paper data exist
    delta_lines = []
    if "paper" in brokers and any(b for b in brokers if b != "paper"):
        live_brokers = [b for b in brokers if b != "paper"]
        for pair in pairs:
            paper_rs = by_broker_pair.get(("paper", pair), [])
            paper_mean = sum(paper_rs) / len(paper_rs) if paper_rs else None
            for lb in live_brokers:
                live_rs = by_broker_pair.get((lb, pair), [])
                live_mean = sum(live_rs) / len(live_rs) if live_rs else None
                if paper_mean is not None and live_mean is not None:
                    delta = live_mean - paper_mean
                    delta_lines.append(f"  {pair:<20s} {lb} − paper = {delta:+.3f}R")
    if delta_lines:
        lines.append("")
        lines.append("Live − paper R-delta per pair:")
        lines.extend(delta_lines)

    return "\n".join(lines)


def _metrics_summary() -> str:
    """Snapshot the in-process Prometheus metrics. Empty when
    prometheus_client isn't installed (the no-op shim has no observed
    values)."""
    try:
        from prometheus_client import REGISTRY
    except ImportError:
        return (
            "shadow metrics — prometheus_client not installed.\n"
            "  Install it (`pip install prometheus-client`) AND scrape\n"
            "  /metrics on :9100 to retain slippage / divergence data."
        )

    lines = ["shadow metrics — current in-process snapshot"]
    lines.append("-" * 60)

    # Slippage histogram per (pair, side).
    slip_samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    diverged_counts: dict[tuple[str, str], float] = defaultdict(float)

    for metric in REGISTRY.collect():
        if metric.name == "ictbot_shadow_fill_slippage_bps":
            for s in metric.samples:
                if s.name.endswith("_sum"):
                    key = (s.labels.get("pair", "?"), s.labels.get("side", "?"))
                    # Histogram sum gives total; we approximate mean via
                    # the matching _count sample. For a single sample
                    # the mean = sum, which is fine here.
                    slip_samples[key].append(s.value)
        elif metric.name == "ictbot_shadow_diverged_total":
            for s in metric.samples:
                key = (s.labels.get("pair", "?"), s.labels.get("reason", "?"))
                diverged_counts[key] = s.value

    if slip_samples:
        lines.append("  Slippage (bps; positive = worse fill than signal price):")
        for (pair, side), vals in sorted(slip_samples.items()):
            try:
                med = median(vals)
                lines.append(f"    {pair:<20} {side:<5} median={med:+.2f}  n={len(vals)}")
            except Exception:
                lines.append(f"    {pair:<20} {side:<5} (no samples)")
    else:
        lines.append("  Slippage : no paired placements observed yet.")

    if diverged_counts:
        lines.append("  Divergences (one leg placed, other didn't):")
        for (pair, reason), n in sorted(diverged_counts.items()):
            if n > 0:
                lines.append(f"    {pair:<20} {reason:<20} count={int(n)}")
    else:
        lines.append("  Divergences : 0.")
    return "\n".join(lines)


def build_report(*, by_broker: bool = False) -> str:
    sections = [_live_summary(), _metrics_summary()]
    if by_broker:
        sections.insert(1, _by_broker_summary())
    return (
        "================ SHADOW REPORT ================\n"
        + "\n\n".join(sections)
        + "\n==============================================="
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Shadow-router comparison report")
    ap.add_argument(
        "--telegram",
        action="store_true",
        help="Also push the report to TELEGRAM_CHAT_ID destinations.",
    )
    ap.add_argument(
        "--by-broker",
        action="store_true",
        help="Add a per-pair R breakdown split by the `broker` journal "
        "field (Fix 2.H). Lets you compare live vs paper/shadow "
        "expectancy without the original ShadowRouter divergence "
        "metrics — handy when prometheus_client isn't installed.",
    )
    args = ap.parse_args()

    report = build_report(by_broker=args.by_broker)
    print(report)

    if args.telegram:
        from ictbot.notify.telegram import send_telegram

        send_telegram(report)


if __name__ == "__main__":
    main()
