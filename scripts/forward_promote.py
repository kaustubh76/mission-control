#!/usr/bin/env python3
"""
Forward-promotion check — the automated arbiter for Part 7's promotion policy.

A backtest cannot prove forward edge. This reads the FORWARD SIM journal
(data/journal/allocator_journal.jsonl), groups REBALANCE rows by the `strategy` field
(added in Phase 0), and per strategy with enough forward history evaluates the three
forward conditions on its own NAV track:

    forward_eligible = worst rolling-7d DD < 25%  AND  >= 7 trades/wk
                       AND  median weekly return >= 0

Strategies with too little forward history report "insufficient forward data" — the
honest, common state until the operator has run each arm forward in SIM for a couple of
weeks (set STRATEGY_NAME / the dashboard selector, then run daily sim ticks). Results
persist to data/reports/strategy_gates.json under each strategy's `forward` sub-key, so
the dashboard selector can badge each arm. This NEVER promotes anything by itself — live
still requires explicit operator sign-off (Part 7).

Usage:
  python scripts/forward_promote.py                 # all registered strategies, print
  python scripts/forward_promote.py --save          # ...and persist the verdicts
  python scripts/forward_promote.py --strategy dual_momentum
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

import numpy as np

from ictbot.api.reads import read_journal
from ictbot.engine.acceptance import DEFAULT as GATE
from ictbot.runtime import verdicts
from ictbot.strategy import registry

MIN_DAYS = 14   # need at least ~2 weeks of forward history to judge
MIN_ROWS = 10
WEEK = timedelta(days=7)


def _parse_ts(s: str):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _strategy_rows(rows: list[dict], name: str) -> list[tuple]:
    """Sorted [(ts, nav_after, n_swaps)] for one strategy's REBALANCE rows."""
    out = []
    for r in rows:
        if r.get("event") != "REBALANCE" or (r.get("strategy") or "") != name:
            continue
        ts = _parse_ts(r.get("ts", ""))
        nav = r.get("nav_after")
        if ts is None or not isinstance(nav, (int, float)) or nav <= 0:
            continue
        out.append((ts, float(nav), int(r.get("n_swaps") or 0)))
    out.sort(key=lambda x: x[0])
    return out


def _forward_stats(rows: list[tuple], *, min_days: float = MIN_DAYS) -> dict | None:
    """Per-strategy forward stats from sorted (ts, nav, n_swaps); None if insufficient.

    `min_days` is the minimum forward span to evaluate (default the rigorous 14d; the
    contest-window campaign passes a compressed value, e.g. 5d — see strategy_campaign.py).
    """
    if len(rows) < MIN_ROWS:
        return None
    ts = [r[0] for r in rows]
    nav = np.array([r[1] for r in rows], dtype=float)
    span_days = (ts[-1] - ts[0]).total_seconds() / 86400.0
    if span_days < min_days:
        return None
    # worst rolling 7-day drawdown (time-based windows)
    worst_dd = 0.0
    for a in range(len(rows)):
        seg = nav[[i for i in range(a, len(rows)) if ts[i] <= ts[a] + WEEK]]
        if len(seg) < 2:
            continue
        peak = np.maximum.accumulate(seg)
        worst_dd = max(worst_dd, float(np.max((peak - seg) / peak)))
    # median weekly return over consecutive 7-day buckets from the first tick
    weekly, b_start = [], ts[0]
    while b_start < ts[-1]:
        seg = [n for (t, n, _) in rows if b_start <= t < b_start + WEEK]
        if len(seg) >= 2:
            weekly.append(seg[-1] / seg[0] - 1.0)
        b_start += WEEK
    median_wk = float(np.median(weekly)) if weekly else None
    tpw = sum(r[2] for r in rows) / (span_days / 7.0) if span_days > 0 else 0.0
    return {
        "worst_7d_dd": round(worst_dd, 4),
        "trades_per_week": round(tpw, 2),
        "median_weekly_ret": (round(median_wk, 4) if median_wk is not None else None),
        "n_rows": len(rows),
        "span_days": round(span_days, 1),
    }


def _eligible(stats: dict) -> bool:
    """Part 7's three forward conditions."""
    mwr = stats.get("median_weekly_ret")
    return (
        stats["worst_7d_dd"] < GATE.max_worst_week_dd
        and stats["trades_per_week"] >= GATE.min_trades_per_week
        and mwr is not None
        and mwr >= 0.0
    )


def _verdict_for(rows: list[dict], name: str, *, min_days: float = MIN_DAYS) -> dict:
    stats = _forward_stats(_strategy_rows(rows, name), min_days=min_days)
    if stats is None:
        return {"status": "insufficient forward data", "forward_eligible": False}
    return {"status": "evaluated", "forward_eligible": _eligible(stats), **stats}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default=None, help="one strategy (default: all registered)")
    ap.add_argument("--save", action="store_true", help="persist verdicts to strategy_gates.json")
    ap.add_argument("--min-days", type=float, default=MIN_DAYS,
                    help=f"minimum forward span to evaluate (default {MIN_DAYS}d; lower for the "
                         "contest-compressed window, e.g. 5)")
    args = ap.parse_args()

    rows = read_journal(limit=5000)
    names = [args.strategy] if args.strategy else registry.available()
    print(f"forward-promotion check ({len(rows)} journal rows; need >= {args.min_days:g}d & "
          f">= {MIN_ROWS} ticks per arm)\n")
    print(f"{'strategy':22} {'status':24} {'7dDD':>7} {'t/wk':>6} {'medWk':>7} {'eligible':>9}")
    print("-" * 80)
    for name in names:
        v = _verdict_for(rows, name, min_days=args.min_days)
        dd = f"{v['worst_7d_dd']*100:5.1f}%" if "worst_7d_dd" in v else "   —"
        tpw = f"{v['trades_per_week']:5.1f}" if "trades_per_week" in v else "   —"
        mwr = (f"{v['median_weekly_ret']*100:+5.1f}%"
               if v.get("median_weekly_ret") is not None else "   —")
        elig = "✅" if v["forward_eligible"] else ("⊘" if v["status"] != "evaluated" else "❌")
        print(f"{name:22} {v['status']:24} {dd:>7} {tpw:>6} {mwr:>7} {elig:>9}")
        if args.save:
            from datetime import timezone
            verdicts.record(name, "forward", {**v,
                            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if args.save:
        print(f"\n[save] forward verdicts persisted to {verdicts.VERDICTS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
