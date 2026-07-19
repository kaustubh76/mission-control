#!/usr/bin/env python3
"""
PnL-campaign sweep: pick the forward-track config with the best shot at +5–7%
in ~9 days, under the campaign rules (10% DD halt + profit-lock ratchet).

Unlike validate_allocator (7-day windows, aggregate stats only), this grid-
searches the allocator levers and — per rolling --days window — REPLAYS the
campaign rules on the window's equity segment, scoring the *campaign outcome*
(banked / trailed / halted / end-of-window), i.e. exactly what the dashboard
NAV would have shown under the ratchet.

Honest by construction: the ratchet converts a good path into a KEPT path; it
cannot manufacture one. The primary metric P(outcome >= +5%) is reported next
to P(halted) and the raw drawdown stats, and the chosen config must clear the
HARD risk gates before the objective is even considered.

Grid (CLI-overridable): cap band x top_k x lookback x rebal cadence x regime
(base / CMC-enhanced macro) x TA-in-cap (off/on) x universe (full / -DOGE /
-DOT / -DOGE-DOT). TA-on-the-RANKING stays out — A/B-proven negative
(docs/cmc_pnl_ab.md).

Usage:
    PYTHONPATH=src python scripts/sweep_campaign.py [--limit 2500] [--days 9]
        [--quick] [--top 15] [--no-write]
    make sweep_campaign
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# Offline RESEARCH — enable the intel fetchers for the historical macro pull
# (same convention as ab_regime.py; live trade gating is untouched).
os.environ.setdefault("CMC_INTEL_ENABLED", "1")

from ictbot.data.cmc import fetch_4h  # noqa: E402
from ictbot.data.cmc_intel import fng_history, global_metrics_history  # noqa: E402
from ictbot.engine.portfolio_replay import (  # noqa: E402
    ONE_WAY_30BPS,
    ONE_WAY_70BPS,
    align_close_matrix,
    returns_matrix,
    simulate,
)
from ictbot.settings import JOURNAL_DIR, settings  # noqa: E402
from ictbot.strategy.macro_align import align_macro_to_index  # noqa: E402
from ictbot.strategy.momentum_allocator import (  # noqa: E402
    CONTEST_TOKENS,
    AllocatorParams,
    weight_path,
)
from ictbot.strategy.regime_score import cap_series, cap_series_enhanced  # noqa: E402

OUT_PATH = JOURNAL_DIR / "campaign_sweep.json"
WARM = 160                     # fixed warmup so every arm scores the same windows
BARS_PER_DAY = 6               # 4h bars

# Campaign rules — mirror the run_allocator defaults (PROFIT_LOCK_* / MAX_DRAWDOWN_FRAC).
# dd_cap is overridable via --dd-cap: a wider halt (still < the 30% DQ line) lets
# winners run to the +5%/+10% profit-lock instead of being stopped out early.
RULES = dict(dd_cap=0.10, trigger=0.05, trail=0.03, min_keep=0.03, bank=0.10)

# The sweep grid. rebal_bars IS the live cron cadence (the runtime rebalances
# every invocation), so 1 = 4h cron, 3 = 12h, 6 = daily.
GRID = dict(
    floor=(0.30, 0.40),
    ceiling=(0.85, 0.90, 0.95),
    top_k=(2, 3, 4, 5, 6, 8),
    lookback=(60, 90, 120),
    rebal_bars=(1, 3, 6),
    regime=("base", "enhanced"),
    ta_cap=(False, True),
)
UNIVERSES = {
    "full": (),
    "-DOGE": ("DOGE",),
    "-DOT": ("DOT",),
    "-DOGE-DOT": ("DOGE", "DOT"),
}
QUICK_GRID = dict(GRID, floor=(0.40,), lookback=(120,), rebal_bars=(3, 6))


def campaign_outcome(seg: np.ndarray, *, dd_cap: float, trigger: float,
                     trail: float, min_keep: float, bank: float) -> tuple[float, str, float]:
    """Replay the campaign rules over ONE window's equity segment (anchor = bar 0).

    Returns (outcome_return, status, realized_dd):
      - status in {end, bank, trail, halt}
      - realized_dd = the max drawdown actually EXPERIENCED up to the exit bar.
        Under campaign rules the DD halt exits at the first >dd_cap breach, so
        this is bounded by dd_cap + a single bar's overshoot — that's the "max
        cap 10%" the user asked for, vs the raw (un-halted) window DD which the
        strategy would otherwise run to ~17%.

    Order mirrors run_allocator: the DD halt is checked first (it wins ties),
    then bank, then the armed trail. Approximation vs live: the HWM starts at
    the window's first bar (no carried-in pre-window HWM)."""
    e0 = float(seg[0])
    peak_all = e0
    armed = False
    peak_since = 0.0
    realized_dd = 0.0
    for x in seg[1:]:
        x = float(x)
        peak_all = max(peak_all, x)
        dd = (peak_all - x) / peak_all
        realized_dd = max(realized_dd, dd)
        if dd > dd_cap:
            return x / e0 - 1.0, "halt", realized_dd
        cum = x / e0 - 1.0
        if cum >= bank:
            return cum, "bank", realized_dd
        if armed:
            peak_since = max(peak_since, x)
            floor_ = max(e0 * (1.0 + min_keep), peak_since * (1.0 - trail))
            if x < floor_:
                return cum, "trail", realized_dd
        elif cum >= trigger:
            armed = True
            peak_since = x
    return float(seg[-1]) / e0 - 1.0, "end", realized_dd


def window_stats(eq: np.ndarray, win: int) -> dict:
    """Per-arm stats over all rolling `win`-bar windows: raw return/DD arrays
    + the campaign-rule outcomes. (Local sibling of rolling_window_stats —
    that helper returns aggregates only and is hardwired to 7-day windows.)"""
    rets, dds, outs, status, rdds = [], [], [], [], []
    for a in range(WARM, len(eq) - win):
        seg = eq[a:a + win + 1]
        if seg[0] <= 0:
            continue
        rets.append(float(seg[-1] / seg[0] - 1.0))
        peak = np.maximum.accumulate(seg)
        dds.append(float(np.max((peak - seg) / peak)))
        o, st, rdd = campaign_outcome(seg, **RULES)
        outs.append(o)
        status.append(st)
        rdds.append(rdd)
    if not rets:
        return {"n_windows": 0}
    rets, dds, outs = np.asarray(rets), np.asarray(dds), np.asarray(outs)
    rdds, status = np.asarray(rdds), np.asarray(status)
    return {
        "n_windows": int(rets.size),
        # campaign-rule outcomes (what the dashboard NAV would show)
        "p_outcome_ge_5": float((outs >= 0.05).mean()),
        "p_outcome_ge_7": float((outs >= 0.07).mean()),
        "p_halted": float((status == "halt").mean()),
        "p_banked": float(np.isin(status, ("bank", "trail")).mean()),
        "median_outcome": float(np.median(outs)),
        "p95_outcome": float(np.percentile(outs, 95)),
        "p5_outcome": float(np.percentile(outs, 5)),
        "worst_outcome": float(np.min(outs)),
        # REALIZED drawdown under the campaign rules (bounded by the 10% halt) —
        # this is the "max cap 10%" the user asked for.
        "worst_realized_dd": float(np.max(rdds)),
        "p_realized_dd_over_10": float((rdds > 0.10).mean()),
        # raw (pre-overlay, un-halted) risk view — for honesty/context only.
        "median_ret_raw": float(np.median(rets)),
        "worst_window_dd_raw": float(np.max(dds)),
        "p_dd_over_30_raw": float((dds > 0.30).mean()),   # contest DQ gate (raw)
    }


def hard_gates(s: dict, trades_wk: float, dd_cap: float) -> bool:
    """DQ-safe + risk-bounded + active. The DD gate is on the campaign-REALIZED
    drawdown (bounded by the halt), not the raw un-halted equity — the halt is
    the whole point. Allow ~1 bar of overshoot past the cap, and never let the
    raw DD reach the 30% contest DQ line in ANY window."""
    return (s.get("n_windows", 0) > 0
            and s["worst_realized_dd"] <= dd_cap + 0.05
            and s["p_dd_over_30_raw"] == 0.0
            and trades_wk >= 7.0)


def _macro(index):
    gm, fng = global_metrics_history(760), fng_history(500)
    am = align_macro_to_index(index, gm, fng) if (gm or fng) else None
    return am if (am is not None and am.any_present()) else None


def _ta_health(mat):
    """Per-4h-bar locally-computed TA trend-health aligned to mat.index (the
    backtestable stand-in for CMC's authoritative daily TA — same as ab_regime)."""
    from ictbot.strategy import technicals as T
    daily = T.resample_daily(mat)
    return T.align_daily_to_index(T.trend_health(daily), daily.index, mat.index)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2500, help="4h bars of history")
    ap.add_argument("--days", type=int, default=9, help="campaign horizon (days/window)")
    ap.add_argument("--dd-cap", type=float, default=0.10,
                    help="campaign drawdown halt (default 0.10; <0.30 DQ line)")
    ap.add_argument("--top", type=int, default=15, help="rows to print")
    ap.add_argument("--quick", action="store_true", help="reduced grid (smoke)")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    win = args.days * BARS_PER_DAY
    RULES["dd_cap"] = float(args.dd_cap)

    frames = {t: fetch_4h(t, args.limit) for t in CONTEST_TOKENS}
    mat_full = align_close_matrix(frames, CONTEST_TOKENS)
    print(f"candles: {mat_full.shape[0]} x {list(mat_full.columns)}  "
          f"window={win} bars ({args.days}d)  rules={RULES}")
    am_full = _macro(mat_full.index)
    if am_full is None:
        print("WARNING: no historical macro — 'enhanced' arms fall back to base regime.")

    grid = QUICK_GRID if args.quick else GRID
    results = []
    cap_cache: dict = {}
    ta_cache: dict = {}
    for uni_name, dropped in UNIVERSES.items():
        cols = [c for c in mat_full.columns if c not in dropped]
        mat = mat_full[cols]
        close = mat.to_numpy(dtype=float)
        rets = returns_matrix(close)
        # NOTE: dropping a column also shrinks the breadth gauge here; the live
        # active_tokens path keeps FULL-universe breadth — a small known mismatch.
        for floor, ceiling, regime, ta_on in product(
                grid["floor"], grid["ceiling"], grid["regime"], grid["ta_cap"]):
            ck = (uni_name, floor, ceiling, regime, ta_on)
            if ck not in cap_cache:
                ta_h = None
                if ta_on:
                    if uni_name not in ta_cache:
                        ta_cache[uni_name] = _ta_health(mat)
                    ta_h = ta_cache[uni_name]
                if regime == "enhanced" and am_full is not None:
                    am = am_full
                    cap_cache[ck] = cap_series_enhanced(
                        close, floor=floor, ceiling=ceiling,
                        ma_window=settings.alloc_breadth_ma,
                        dominance=am.dominance, dominance_prev=am.dominance_prev,
                        mktcap=am.mktcap, mktcap_prev=am.mktcap_prev,
                        fng=am.fng, fng_7d=am.fng_7d, ta_health=ta_h)
                elif ta_h is not None:
                    cap_cache[ck] = cap_series_enhanced(
                        close, floor=floor, ceiling=ceiling,
                        ma_window=settings.alloc_breadth_ma, ta_health=ta_h)
                else:
                    cap_cache[ck] = cap_series(
                        close, floor=floor, ceiling=ceiling,
                        ma_window=settings.alloc_breadth_ma)
            caps = cap_cache[ck]
            for top_k, lookback, rebal in product(
                    grid["top_k"], grid["lookback"], grid["rebal_bars"]):
                p = AllocatorParams(lookback=lookback, top_k=top_k,
                                    vol_lookback=settings.alloc_vol_lookback,
                                    rebal_bars=rebal, abs_filter=False)
                w = weight_path(close, p, cap_series=caps)
                eq, txns = simulate(w, rets, ONE_WAY_70BPS)
                trades_wk = txns / max(1.0, (len(eq) - WARM) / 42.0)
                s = window_stats(eq, win)
                arm = {
                    "universe": uni_name, "floor": floor, "ceiling": ceiling,
                    "regime": regime, "ta_cap": ta_on, "top_k": top_k,
                    "lookback": lookback, "rebal_bars": rebal,
                    "trades_per_week": round(trades_wk, 1),
                    "passes_hard_gates": hard_gates(s, trades_wk, RULES["dd_cap"]),
                    **{k: (round(v, 4) if isinstance(v, float) else v)
                       for k, v in s.items()},
                }
                results.append(arm)
    print(f"swept {len(results)} arms in {time.time() - t0:.0f}s "
          f"(cap-series cache: {len(cap_cache)})")

    # Rank: hard gates first, then the objective (median >= 0 -> P(>=5%) -> tiebreaks).
    def key(a):
        return (a["passes_hard_gates"],
                a.get("median_outcome", -9) >= 0,
                a.get("p_outcome_ge_5", 0),
                a.get("median_outcome", -9),
                a.get("p95_outcome", -9),
                -a.get("p_halted", 9))
    results.sort(key=key, reverse=True)

    # 30 bps sanity re-run for the top arms (cheap: weight paths recompute fast).
    for a in results[:max(args.top, 25)]:
        cols = [c for c in mat_full.columns if c not in UNIVERSES[a["universe"]]]
        close = mat_full[cols].to_numpy(dtype=float)
        caps = cap_cache[(a["universe"], a["floor"], a["ceiling"], a["regime"], a["ta_cap"])]
        p = AllocatorParams(lookback=a["lookback"], top_k=a["top_k"],
                            vol_lookback=settings.alloc_vol_lookback,
                            rebal_bars=a["rebal_bars"], abs_filter=False)
        eq30, _ = simulate(weight_path(close, p, cap_series=caps),
                           returns_matrix(close), ONE_WAY_30BPS)
        s30 = window_stats(eq30, win)
        a["p_outcome_ge_5_30bps"] = round(s30.get("p_outcome_ge_5", 0.0), 4)
        a["median_outcome_30bps"] = round(s30.get("median_outcome", 0.0), 4)

    hdr = (f"{'#':>3} {'universe':>9} {'flr':>4} {'ceil':>5} {'regime':>8} {'ta':>3} "
           f"{'k':>2} {'lb':>4} {'rb':>3} {'P>=5%':>6} {'P>=7%':>6} {'P(halt)':>7} "
           f"{'med':>7} {'p95':>7} {'rDD':>6} {'worst':>7} {'tr/wk':>6} {'gates':>5}")
    print("\n" + hdr)
    print("-" * len(hdr))
    for i, a in enumerate(results[:args.top], 1):
        print(f"{i:>3} {a['universe']:>9} {a['floor']:>4.2f} {a['ceiling']:>5.2f} "
              f"{a['regime']:>8} {str(a['ta_cap'])[:1]:>3} {a['top_k']:>2} "
              f"{a['lookback']:>4} {a['rebal_bars']:>3} "
              f"{a['p_outcome_ge_5']:>6.1%} {a['p_outcome_ge_7']:>6.1%} "
              f"{a['p_halted']:>7.1%} {a['median_outcome']:>7.2%} "
              f"{a['p95_outcome']:>7.2%} {a['worst_realized_dd']:>6.1%} "
              f"{a['worst_outcome']:>7.2%} {a['trades_per_week']:>6.1f} "
              f"{'PASS' if a['passes_hard_gates'] else 'fail':>5}")

    print(f"\n(dd_cap={RULES['dd_cap']:.0%}; realized-DD gate <= {RULES['dd_cap']+0.05:.0%}; "
          f"raw 30% DQ gate; trades/wk >= 7)")
    best = results[0] if results else None
    if best and best["passes_hard_gates"]:
        print(f"\nWINNER: {best['universe']} floor={best['floor']} ceiling={best['ceiling']} "
              f"regime={best['regime']} ta_cap={best['ta_cap']} top_k={best['top_k']} "
              f"lookback={best['lookback']} rebal_bars={best['rebal_bars']} "
              f"-> P(outcome>=5%)={best['p_outcome_ge_5']:.1%}, "
              f"P(halt)={best['p_halted']:.1%}, median={best['median_outcome']:.2%}, "
              f"worst={best['worst_outcome']:.2%}")
        print("Deploy: ALLOC_CAP_FLOOR / ALLOC_CAP_CEILING / ALLOC_TOP_K / ALLOC_LOOKBACK "
              "/ ALLOC_REBAL_BARS (+ cron cadence!) in .env; universe via the dashboard "
              "token toggles (data/journal/active_tokens.json).")
    else:
        print("\nNO ARM passes the hard gates — do not chase the objective; "
              "stay on the validated baseline.")

    if not args.no_write:
        OUT_PATH.write_text(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "limit": args.limit, "days": args.days, "win_bars": win, "warm": WARM,
            "rules": dict(RULES), "friction_primary": ONE_WAY_70BPS,
            "n_arms": len(results), "results": results[:200],
        }, indent=1))
        print(f"wrote {OUT_PATH} (top {min(len(results), 200)} arms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
