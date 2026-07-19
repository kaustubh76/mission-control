#!/usr/bin/env python3
"""
Multi-day P&L curve for `momentum_cmc` — the honest "what would this have done" view.

CMC has no 4h history, so a real multi-day P&L is built by REPLAYING the CMC-driven arm's decisions
over the accumulated CMC **daily** candles (geo-open, CEX-free) at the LIVE config, and extracting the
equity curve via the existing portfolio_replay engine (`simulate` returns the NAV series). Part B reads
the REAL forward journal so the live P&L is shown as it accrues over the contest.

Honest by construction — NO edge claim: long-only spot over a bear-dominated window is negative by
design. The value is survival (DQ-safe) + participation; the real P&L is the live contest week.

NOTE on the window: for DAILY candles a contest week = 7 bars, so the rolling distribution here uses
win=7 (validate_allocator uses the 4h BARS_PER_WEEK=42 = a 42-DAY window on daily data — coarser). The
window-independent totals (total_return, max_dd) match validate_allocator exactly (same engine).

Usage:  CMC_INTEL_ENABLED=true make cmc_pnl   [ARGS="--with-ta --days 730 --start-nav 1000"]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ictbot.data.cmc import daily_close_matrix
from ictbot.engine.portfolio_replay import (
    ONE_WAY_70BPS,
    curve_metrics,
    returns_matrix,
    rolling_window_stats,
    simulate,
)
from ictbot.settings import DATA_DIR, settings
from ictbot.strategy.momentum_allocator import AllocatorParams, weight_path
from ictbot.strategy.regime_score import cap_series

WEEK_BARS = 7  # a contest week on the DAILY grid


def _sparkline(vals: list[float]) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return blocks[0] * len(vals)
    return "".join(blocks[int((v - lo) / (hi - lo) * (len(blocks) - 1))] for v in vals)


def _downsample(arr: np.ndarray, n: int) -> list[float]:
    if len(arr) <= n:
        return list(arr)
    idx = np.linspace(0, len(arr) - 1, n).astype(int)
    return [float(arr[i]) for i in idx]


def _equity_curve(close_df: pd.DataFrame, *, with_ta: bool):
    """Replay momentum_cmc on the daily matrix at the LIVE config -> (eq, txns)."""
    close = close_df.to_numpy()
    p = AllocatorParams(
        lookback=20,
        vol_lookback=10,
        rebal_bars=1,
        top_k=settings.alloc_top_k,
        abs_filter=settings.alloc_abs_filter,
    )
    caps = cap_series(
        close,
        floor=settings.alloc_cap_floor,
        ceiling=settings.alloc_cap_ceiling,
        ma_window=settings.alloc_breadth_ma,
    )
    if with_ta:
        # match the live arm's ta_rank tilt (CMC technicals, locally computed for the backtest)
        from ictbot.strategy.momentum_allocator import weight_path_ranked

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from ab_regime import _ta

        _, ta_score = _ta(close_df)
        w = weight_path_ranked(
            close,
            p,
            cap_series=caps,
            blend={p.lookback: 1.0},
            ta_score=ta_score,
            w_ta_rank=settings.alloc_ta_w_rank,
            tokens=tuple(close_df.columns),
        )
    else:
        w = weight_path(close, p, cap_series=caps)
    eq, txns = simulate(w, returns_matrix(close), ONE_WAY_70BPS)
    return eq, txns, p


def _best_worst_week(eq: np.ndarray, index, warmup: int):
    """The single best/worst 7-bar (daily) window return + its end date."""
    best = (None, -1e9, None)
    worst = (None, 1e9, None)
    for a in range(warmup, len(eq) - WEEK_BARS):
        if eq[a] <= 0:
            continue
        r = eq[a + WEEK_BARS] / eq[a] - 1.0
        if r > best[1]:
            best = (a, r, index[a + WEEK_BARS])
        if r < worst[1]:
            worst = (a, r, index[a + WEEK_BARS])
    return best, worst


def _slice_return(eq: np.ndarray, bars: int):
    """Return + max-DD over the last `bars` of the equity curve."""
    if len(eq) <= bars:
        seg = eq
    else:
        seg = eq[-bars - 1 :]
    if seg[0] <= 0:
        return None, None
    ret = seg[-1] / seg[0] - 1.0
    peak = np.maximum.accumulate(seg)
    dd = float(np.max((peak - seg) / peak))
    return ret, dd


def _replay_report(args) -> int:
    print(
        f"\n{'=' * 78}\n  momentum_cmc — REPLAY P&L on CMC DAILY candles (CEX-free, live config)\n{'=' * 78}"
    )
    close_df = daily_close_matrix(days=args.days)
    if close_df is None or close_df.shape[0] < 60 or close_df.shape[1] < 3:
        print(
            "  ERROR: not enough CMC daily history (need CMC_INTEL_ENABLED=true + CMC daily OHLCV)."
        )
        return 2
    eq, txns, p = _equity_curve(close_df, with_ta=args.with_ta)
    idx = list(close_df.index)
    n = len(eq)
    warmup = p.lookback + 5
    s0 = float(args.start_nav)
    nav = eq * s0
    span_days = (idx[-1] - idx[0]).days
    cm = curve_metrics(eq)
    tpw = txns * WEEK_BARS / (n - 1) if n > 1 else 0.0

    print(
        f"  config: top_k={p.top_k}  band=[{settings.alloc_cap_floor:.2f},{settings.alloc_cap_ceiling:.2f}]"
        f"  ta_rank={'ON' if args.with_ta else 'off'}  friction=0.70%RT  tokens={list(close_df.columns)}"
    )
    print(f"  window: {idx[0].date()} -> {idx[-1].date()}  ({span_days} days, {n} daily bars)\n")
    print(f"  START NAV  ${s0:,.0f}")
    print(f"  END   NAV  ${nav[-1]:,.0f}")
    print(f"  TOTAL P&L  {cm['total_return'] * 100:+.1f}%   (${nav[-1] - s0:+,.0f})")
    print(f"  max drawdown   {cm['max_dd'] * 100:.1f}%")
    print(f"  trades/week    {tpw:.1f}   (rebalances daily; ~{tpw / 7:.1f}/day)\n")

    # trajectory — monthly NAV samples + a sparkline of the whole curve
    print("  NAV trajectory (sampled):")
    samp_i = np.linspace(warmup, n - 1, min(12, n - warmup)).astype(int)
    for i in samp_i:
        print(f"    {idx[i].date()}   ${nav[i]:,.0f}   ({nav[i] / s0 - 1.0:+.1%})")
    print(f"\n  curve: {_sparkline(_downsample(nav[warmup:], 56))}")
    print(f"         {idx[warmup].date()} {' ' * 36} {idx[-1].date()}\n")

    # contest-week range (TRUE 7-day window on daily data)
    wk = rolling_window_stats(eq, warmup=warmup, win=WEEK_BARS)
    if wk.get("n_windows"):
        print(f"  CONTEST-WEEK RANGE (every rolling 7-day window, n={wk['n_windows']}):")
        print(
            f"    mean {wk['mean_ret'] * 100:+.1f}%   median {wk['median_ret'] * 100:+.1f}%"
            f"   p5 {wk['p5_ret'] * 100:+.1f}%   p95 {wk['p95_ret'] * 100:+.1f}%"
        )
        print(
            f"    weeks positive: {wk['pct_up'] * 100:.0f}%   worst-week DD: {wk['worst_week_dd'] * 100:.1f}%"
            f"   weeks > 30% DD (DQ): {wk['pct_dd_over_30'] * 100:.1f}%"
        )
        best, worst = _best_worst_week(eq, idx, warmup)
        print(f"    BEST week  {best[1] * 100:+.1f}%  (ending {best[2].date()})")
        print(f"    WORST week {worst[1] * 100:+.1f}%  (ending {worst[2].date()})\n")

    # recent slices
    print("  RECENT P&L (end of the CMC-daily window):")
    for label, bars in (("last 30 days", 30), ("last 90 days", 90)):
        r, dd = _slice_return(eq, bars)
        if r is not None:
            print(f"    {label:13} {r * 100:+6.1f}%   (max DD {dd * 100:.1f}%)")
    return 0


def _forward_rows(path: Path) -> list[tuple]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") != "REBALANCE" or (r.get("strategy") or "") != "momentum_cmc":
            continue
        try:
            ts = datetime.fromisoformat(r["ts"])
            nav = float(r["nav_after"])
        except Exception:
            continue
        if nav > 0:
            rows.append((ts, nav, int(r.get("n_swaps") or 0)))
    rows.sort(key=lambda x: x[0])
    return rows


def _forward_report() -> None:
    print(
        f"\n{'=' * 78}\n  momentum_cmc — REAL forward P&L so far (live journals; grows over the contest)\n{'=' * 78}"
    )
    tracks = {
        "isolated forward_track_cmc": DATA_DIR
        / "forward"
        / "momentum_cmc"
        / "journal"
        / "allocator_journal.jsonl",
        "production SIM (dashboard)": DATA_DIR / "journal" / "allocator_journal.jsonl",
    }
    any_rows = False
    for name, path in tracks.items():
        rows = _forward_rows(path)
        if not rows:
            print(f"  {name}: no momentum_cmc ticks yet.")
            continue
        any_rows = True
        nav = np.array([r[1] for r in rows])
        span_h = (rows[-1][0] - rows[0][0]).total_seconds() / 3600.0
        peak = np.maximum.accumulate(nav)
        max_dd = float(np.max((peak - nav) / peak)) if len(nav) else 0.0
        pnl = nav[-1] / nav[0] - 1.0
        print(f"  {name}: {len(rows)} ticks over {span_h:.1f}h")
        print(
            f"    NAV ${nav[0]:,.2f} -> ${nav[-1]:,.2f}   P&L {pnl * 100:+.2f}%   max DD {max_dd * 100:.1f}%"
        )
    if any_rows:
        print(
            "\n  (Only a handful of ticks so far — this is the REAL number; it matures tick-by-tick"
        )
        print("   over the contest week via the forward_tick.sh / forward_track_cmc crons.)")


def _caveats() -> None:
    print(f"\n{'=' * 78}\n  HONEST CAVEATS\n{'=' * 78}")
    print(
        "  • CMC DAILY is a CONSERVATIVE proxy for the live 4h feed (daily is coarser/riskier -> more DD)."
    )
    print(
        "  • The replay = candles + regime cap. The LIVE-only brakes (derivatives/macro/Skills overview)"
    )
    print(
        "    only LOWER exposure further (not replayable); ta_rank (--with-ta) adds a small selection tilt."
    )
    print(
        "  • NO EDGE CLAIM. Long-only spot over a bear-dominated 2-yr window is negative by design — the"
    )
    print(
        "    value is survival (DQ-safe, worst-week DD « 30%) + participation, validated FORWARD."
    )
    print("  • The window-independent totals (total_return, max_dd) match `make validate_allocator")
    print(
        "    --candle-source cmc_daily` exactly (same engine); the weekly view here uses a true 7-day window."
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Multi-day P&L curve for momentum_cmc (CMC-daily replay + live)."
    )
    ap.add_argument("--days", type=int, default=730, help="CMC daily history window")
    ap.add_argument(
        "--start-nav", type=float, default=1000.0, help="normalize the curve to this start NAV"
    )
    ap.add_argument(
        "--with-ta", action="store_true", help="include the live ta_rank tilt (CMC technicals)"
    )
    args = ap.parse_args()
    rc = _replay_report(args)
    _forward_report()
    _caveats()
    return rc


if __name__ == "__main__":
    sys.exit(main())
