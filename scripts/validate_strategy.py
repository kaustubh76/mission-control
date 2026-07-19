#!/usr/bin/env python3
"""
Generic Gate-A validator for ANY registered portfolio strategy.

This is the template every new long-only-spot strategy is judged by: load 4h OHLCV
for the 8-token contest universe, run the strategy's vectorised weight path through
the strategy-agnostic portfolio backtest (engine/portfolio_replay) at the BINDING
~0.70% spot-DEX friction, and print the rolling-7-day return/drawdown distribution +
the shared acceptance-gate verdict (engine/acceptance) — the same gate
validate_allocator uses for the locked strategy.

PROMOTION POLICY (uniform — applies to EVERY non-default arm):
  The locked `momentum_adaptive` is the only default. An arm becomes LIVE-eligible only
  after ALL of:
    1. it clears the SURVIVAL gate in backtest here (DQ-safe < 25% worst-week DD AND
       >= 7 trades/wk) — note this is a survival test, NOT an edge test: there is no
       long-only TA edge on this universe (docs/bnb_strategy_decision.md §1), so most
       diversified, regime-capped arms clear it;
    2. it clears a FORWARD check on unseen daily SIM data — worst rolling-7d DD < 25%,
       >= 7 trades/wk, AND a NON-NEGATIVE median weekly return (the backtest cannot
       prove forward edge; the forward run is the arbiter, mirroring `make forward_report`
       for the locked allocator);
    3. explicit operator sign-off (STRATEGY_NAME=<name> + ENABLE_LIVE_TRADING).
  No arm auto-promotes; nothing routes a non-default arm into the contest path. Arms with
  an adverse mechanism prior (e.g. mean_reversion — reversal inverts to momentum on
  majors) are eligible under the SAME rule but a forward PASS is likely sample-luck —
  treat with extra skepticism.

Usage:
  python scripts/validate_strategy.py --strategy dual_momentum
  python scripts/validate_strategy.py --strategy momentum_fast --limit 2500
  python scripts/validate_strategy.py --strategy momentum --static-cap
  python scripts/validate_strategy.py --list
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

import pandas as pd

from ictbot.data.cmc import cmc_4h_close_matrix, daily_close_matrix
from ictbot.engine.acceptance import evaluate_portfolio
from ictbot.engine.portfolio_replay import (
    ONE_WAY_30BPS,
    ONE_WAY_70BPS,
    align_close_matrix,
    evaluate,
)
from ictbot.settings import settings
from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.regime_score import cap_series


def _fmt_row(label, s):
    return (
        f"{label:24} {s['mean_ret'] * 100:+7.2f}% {s['median_ret'] * 100:+7.2f}% "
        f"{s['p5_ret'] * 100:+6.1f}% {s['p95_ret'] * 100:+6.1f}% {s['pct_up'] * 100:4.0f}% "
        f"{s['worst_week_dd'] * 100:7.1f}% {s['pct_dd_over_30'] * 100:5.1f}% "
        f"{s['trades_per_week']:5.1f}"
    )


def load_matrix(limit: int = 2500, *, candle_source: str = "cmc_4h"):
    """Aligned close matrix for the contest universe — the CEX-FREE validation feed.

    DEFAULT cmc_4h = CoinMarketCap's OWN 4h candles (the WebSocket stream + cold-start daily seed —
    the SAME feed the live arm trades on). cmc_daily = real CMC 24-month daily OHLCV (a deep-history
    reference). binance_4h = the legacy CEX reference (self-blocks under the CMC_ONLY firewall)."""
    if candle_source == "cmc_daily":
        days = limit if limit < 1100 else 730
        return daily_close_matrix(CONTEST_TOKENS, days=days)
    if candle_source == "cmc_4h":
        return cmc_4h_close_matrix(CONTEST_TOKENS)
    # binance_4h — guarded dev reference (fetch_4h raises under CMC_ONLY)
    from ictbot.data.cmc import fetch_4h

    return align_close_matrix({t: fetch_4h(t, limit) for t in CONTEST_TOKENS}, CONTEST_TOKENS)


def load_frames(limit: int = 2500, *, candle_source: str = "cmc_4h") -> dict:
    """Per-token {token: [time, close] frame} for the universe — DERIVED from load_matrix so a
    multi-arm caller (scripts/strategy_campaign.py) can fetch ONCE and reuse via align_close_matrix.
    CEX-free by default (CMC's own 4h candles). align_close_matrix only reads time+close, so
    close-only frames reconstruct the identical matrix."""
    mat = load_matrix(limit, candle_source=candle_source)
    return {t: pd.DataFrame({"time": mat.index, "close": mat[t].to_numpy()}) for t in mat.columns}


def daily_rescale(p: AllocatorParams) -> AllocatorParams:
    """Coarsen 4h-bar horizons to the DAILY grid (6 x 4h = 1 day), floored at sane mins, so an arm's
    native 4h params validate correctly on CMC daily candles (else a 120-bar lookback = 120 DAYS).
    Mirrors validate_allocator's hand-rescale (lookback 120→20, rebal 6→1). The arm-specific window
    params (breakout entry_lb, grid/mean_reversion window) live in the adapter ctor and are coarsened
    separately via each arm's optional for_daily() hook."""
    return replace(
        p,
        lookback=max(2, round(p.lookback / 6)),
        vol_lookback=max(2, round(p.vol_lookback / 6)),
        rebal_bars=max(1, round(p.rebal_bars / 6)),
    )


def survival_for(
    strategy: str,
    *,
    limit: int = 2500,
    static_cap: bool = False,
    frames: dict | None = None,
    candle_source: str = "cmc_4h",
) -> dict:
    """Run the shared backtest-survival gate for one registered strategy — CEX-FREE.

    The single source of truth for the survival verdict: both this CLI and
    scripts/strategy_campaign.py call it, so the campaign and the per-arm command can
    never drift. DEFAULT source `cmc_4h` = CoinMarketCap's OWN 4h candles (the WebSocket
    stream + cold-start daily seed — the SAME feed the live arm trades on), so the gate
    runs the arm's NATIVE 4h params at the TRUE 7-day window (42 x 4h bars). No exchange
    data. (Optional `cmc_daily` = real CMC 24-month daily as a deep-history reference; it
    coarsens the 4h horizons via daily_rescale() + each arm's for_daily() hook — coarser, a
    conservative proxy.) The flat-intrabar seed is sized correctly by injecting the shared
    cmc_seed_vol_floor into the params (same as the live tick), so inverse-vol doesn't blow up.

    Raises KeyError if `strategy` is not registered (the caller maps that to its own exit
    code). Returns a dict; `ok=False` means too little aligned data to judge.
    """
    daily = candle_source == "cmc_daily"
    strat = registry.get(strategy)
    if daily and hasattr(strat, "for_daily"):
        strat = strat.for_daily()  # coarsen the adapter's ctor windows to the daily grid
    p = strat.default_params()
    if daily:
        p = daily_rescale(p)  # coarsen the AllocatorParams horizons to the daily grid
    if frames is None:
        mat = load_matrix(limit, candle_source=candle_source)
    else:
        mat = align_close_matrix(frames, CONTEST_TOKENS)
    # cmc_4h: the cold-start seed is flat intrabar (5-of-6 returns = 0) → inverse-vol 1/vol blows up.
    # Inject the SAME daily-derived floor the live tick uses, so the backtest sizes the held set
    # correctly (relaxes to a no-op as real streamed 4h bars accrue). Never on cmc_daily (real bars).
    if candle_source == "cmc_4h":
        from ictbot.strategy.momentum_allocator import cmc_seed_vol_floor

        _vf = cmc_seed_vol_floor(mat)
        if _vf > 0:
            p = replace(p, vol_floor=_vf)
    have = list(mat.columns)
    base = {
        "summary": strat.summary(p, n_tokens=mat.shape[1]),
        "n_loaded": len(have),
        "n_bars": int(mat.shape[0]),
        "n_tokens": int(mat.shape[1]),
        "cols": list(mat.columns),
        "candle_source": candle_source,
    }
    min_bars = 200 if daily else 400  # daily: 730 bars; 4h: thousands (seed + stream)
    if mat.shape[0] < min_bars or mat.shape[1] < 3:
        return {**base, "ok": False, "error": "not enough aligned data"}

    close = mat.to_numpy()
    floor, ceiling = settings.alloc_cap_floor, settings.alloc_cap_ceiling
    caps_arr = (
        None
        if static_cap
        else cap_series(close, floor=floor, ceiling=ceiling, ma_window=settings.alloc_breadth_ma)
    )
    cap_note = "static deploy_cap" if static_cap else f"regime cap [{floor:.2f},{ceiling:.2f}]"
    warmup = max(40 if daily else 160, strat.warmup(p))  # 4h native = 160; daily = 40

    wp = strat.weight_path(close, p=p, cap_series=caps_arr)
    stats_30 = evaluate(close, wp, one_way=ONE_WAY_30BPS, warmup=warmup)
    stats_70 = evaluate(close, wp, one_way=ONE_WAY_70BPS, warmup=warmup)
    # Verdict at the BINDING spot-DEX friction, via the shared Gate-A gate.
    return {
        **base,
        "ok": True,
        "error": None,
        "cap_note": cap_note,
        "stats_30": stats_30,
        "stats_70": stats_70,
        "gate": evaluate_portfolio(stats_70),
    }


def survival_payload(gate, stats: dict, ts: str) -> dict:
    """The persisted backtest-survival verdict — the SINGLE source of truth for the
    `survival` key in data/reports/strategy_gates.json. Used by this CLI's --save-verdict
    AND scripts/strategy_campaign.py, so the two writers can never drift (the dashboard
    badges arms from this exact field set)."""
    return {
        "passed": gate.passed,
        "worst_week_dd": round(stats["worst_week_dd"], 4),
        "trades_per_week": round(stats["trades_per_week"], 2),
        "within_dq_line": gate.metrics["within_dq_line"],
        "target_dd_met": gate.metrics["target_dd_met"],
        "ts": ts,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="dual_momentum")
    ap.add_argument("--limit", type=int, default=2500)
    ap.add_argument(
        "--static-cap",
        action="store_true",
        help="use the static deploy_cap instead of the regime cap_series",
    )
    ap.add_argument(
        "--candle-source",
        choices=["cmc_4h", "cmc_daily", "binance_4h"],
        default="cmc_4h",
        help="DEFAULT cmc_4h = CMC's own 4h candles (WS stream + seed — the feed the live arm trades "
        "on); cmc_daily = deep-history daily reference; binance_4h = legacy CEX (blocked under CMC_ONLY).",
    )
    ap.add_argument("--list", action="store_true", help="list registered strategies and exit")
    ap.add_argument(
        "--save-verdict",
        action="store_true",
        help="persist the backtest-survival verdict to data/reports/strategy_gates.json",
    )
    args = ap.parse_args()

    if args.list:
        print("registered strategies:", ", ".join(registry.available()))
        return 0

    if settings.cmc_only and args.candle_source == "binance_4h":
        print(
            "ERROR: CMC_ONLY=true forbids --candle-source binance_4h (CEX). Use cmc_daily / cmc_4h."
        )
        return 2

    try:
        rep = survival_for(
            args.strategy,
            limit=args.limit,
            static_cap=args.static_cap,
            candle_source=args.candle_source,
        )
    except KeyError as e:
        print(f"ERROR: {e}")
        return 2

    print(f"strategy '{args.strategy}': {rep['summary']}")
    print(
        f"loaded {rep['n_loaded']}/{len(CONTEST_TOKENS)} tokens; aligned matrix "
        f"{rep['n_bars']} bars x {rep['n_tokens']} ({rep['cols']})"
    )
    if not rep["ok"]:
        print("ERROR: not enough aligned data to validate.")
        return 2

    s, gate = rep["stats_70"], rep["gate"]
    print(f"\n[deployment] {rep['cap_note']}")
    print(
        f"\n{'config':24} {'meanRet':>8} {'medRet':>8} {'p5':>7} {'p95':>7} "
        f"{'%up':>5} {'wkDDmax':>8} {'%>30':>6} {'t/wk':>6}"
    )
    print("-" * 86)
    print(_fmt_row("0.30%RT", rep["stats_30"]))
    print(_fmt_row("0.70%RT (BINDING)", s))

    print("\n" + "=" * 86)
    print(f"GATE A ({args.strategy} @ 0.70% spot-DEX friction):")
    print(
        f"  worst-week DD {s['worst_week_dd'] * 100:.1f}%  -> within 25% ceiling: "
        f"{'✅' if gate.dq_safe else '❌'}"
        f"   (30% DQ: {'✅' if gate.metrics['within_dq_line'] else '❌'}, "
        f"15% target: {'✅' if gate.metrics['target_dd_met'] else '⚠️'})"
    )
    print(
        f"  trades/week  {s['trades_per_week']:.1f}  -> active(>=7): "
        f"{'✅' if gate.active else '❌'}"
    )
    if gate.passed:
        print(f"VERDICT: ✅ PASS  -> '{args.strategy}' clears Gate A on the contest universe.")
        print("         Next: forward-validate in SIM before any LIVE promotion.")
    else:
        print(f"VERDICT: ❌ FAIL  -> {'; '.join(gate.reasons)}")
        print("         Do NOT promote to LIVE. Keep on the SIM/research track.")
    print("  HONEST NOTE: there is no proven long-only TA edge on these 8 tokens")
    print("  (docs/bnb_strategy_decision.md §1). A PASS = DQ-safe + active, NOT alpha.")
    print("=" * 86)

    if args.save_verdict:
        from datetime import datetime, timezone

        from ictbot.runtime import verdicts

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        verdicts.record(args.strategy, "survival", survival_payload(gate, s, ts))
        print(f"[save-verdict] survival verdict persisted to {verdicts.VERDICTS_FILE}")
    return 0 if gate.passed else 1


if __name__ == "__main__":
    sys.exit(main())
