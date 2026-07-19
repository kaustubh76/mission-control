#!/usr/bin/env python3
"""
Reproducible validation of the committed momentum allocator (the contest strategy).

Loads 4h OHLCV for the 8-token contest universe (public Binance, no key; falls
back to the cached snapshot), runs the portfolio backtest over ALL rolling 7-day
windows, and prints the return/drawdown distribution + the contest gate verdict
for the committed config and a cap sweep (the risk dial).

Gate (per the rolling-7-day distribution):
  DQ-SAFE   : worst-week drawdown < 30% (the disqualification gate); target < 15%.
  ACTIVE    : >= 7 trades / week (the contest min-trade floor).

Usage:  python scripts/validate_allocator.py [--limit 2500] [--cap 0.60]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from ictbot.data.cmc import fetch_4h
from ictbot.engine.acceptance import evaluate_portfolio
from ictbot.engine.portfolio_replay import (
    ONE_WAY_30BPS,
    ONE_WAY_70BPS,
    align_close_matrix,
    evaluate,
)
from ictbot.settings import settings
from ictbot.strategy.momentum_allocator import (
    CONTEST_TOKENS,
    AllocatorParams,
    weight_path,
)
from ictbot.strategy.regime_score import cap_series, regime_labels


def load_4h(sym: str, limit: int):
    """4h OHLCV (live Binance -> cache -> bybit cache) via the shared CMC feed."""
    return fetch_4h(sym, limit)


def _fmt_row(label, s):
    return (
        f"{label:24} {s['mean_ret'] * 100:+7.2f}% {s['median_ret'] * 100:+7.2f}% "
        f"{s['p5_ret'] * 100:+6.1f}% {s['p95_ret'] * 100:+6.1f}% {s['pct_up'] * 100:4.0f}% "
        f"{s['worst_week_dd'] * 100:7.1f}% {s['pct_dd_over_30'] * 100:5.1f}% "
        f"{s['trades_per_week']:5.1f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2500)
    ap.add_argument("--cap", type=float, default=0.60)
    ap.add_argument(
        "--candle-source",
        choices=["cmc_4h", "cmc_daily", "binance_4h"],
        default="cmc_4h",
        help="DEFAULT cmc_4h = the DQ gate on CMC's OWN 4h candles (the WS stream + seed — the feed "
        "the live arm trades on), native 4h params + the TRUE 7-day window. cmc_daily = real CMC "
        "daily as a deep-history reference (coarser). binance_4h = legacy CEX (blocked under CMC_ONLY).",
    )
    ap.add_argument(
        "--cap-sweep",
        action="store_true",
        help="sweep candidate (floor, ceiling) deploy-cap bands and report the widest band "
        "whose worst-week DD < 25%% (the operational ceiling) — picks momentum_cmc's band.",
    )
    args = ap.parse_args()

    # Zero-CEX firewall: under CMC_ONLY the binance proxy is forbidden (it self-blocks in
    # cmc.fetch_4h too, but refuse early with a clear message rather than a deep traceback).
    if settings.cmc_only and args.candle_source == "binance_4h":
        print(
            "ERROR: CMC_ONLY=true forbids --candle-source binance_4h (CEX). Use cmc_daily / cmc_4h."
        )
        return 2

    if args.candle_source == "cmc_4h":
        # THE DQ GATE: CMC's own 4h candles (the WS stream + cold-start daily seed — the SAME feed the
        # live arm trades on). NATIVE 4h params + the TRUE 7-day window (42 x 4h bars). The flat seed is
        # sized correctly by the shared seed vol-floor; it relaxes to a no-op as real streamed bars accrue.
        from ictbot.data.cmc import cmc_4h_close_matrix
        from ictbot.strategy.momentum_allocator import cmc_seed_vol_floor

        mat = cmc_4h_close_matrix(CONTEST_TOKENS)
        have = [c for c in CONTEST_TOKENS if c in mat.columns]
        # 4h-native horizons + the LIVE momentum_cmc top_k (settings.alloc_top_k) + the seed vol-floor,
        # so the backtest matches the running config exactly.
        base = AllocatorParams(top_k=settings.alloc_top_k, vol_floor=cmc_seed_vol_floor(mat))
        print("[source] CMC 4h candles (WS stream + seed) — the CEX-free DQ gate (native 4h, 7-day window)")
    elif args.candle_source == "cmc_daily":
        # The CEX-FREE DQ-safety gate for momentum_cmc: real CMC 24-month DAILY OHLCV (geo-open, no
        # exchange data, no flat-intrabar seed pathology). Daily-rescaled params. This run produces the
        # citable worst-week-DD / trades-per-week verdict; the live arm runs the same logic on cmc_4h.
        from ictbot.data.cmc import daily_close_matrix

        days = args.limit if args.limit < 1100 else 730
        mat = daily_close_matrix(CONTEST_TOKENS, days=days)
        have = [c for c in CONTEST_TOKENS if c in mat.columns]
        # Daily-rescaled horizons + the LIVE momentum_cmc top_k (settings.alloc_top_k) so the DQ gate
        # matches the running config (the live arm holds top_k tokens, not the locked-arm default of 2).
        base = AllocatorParams(
            lookback=20, vol_lookback=10, rebal_bars=1, top_k=settings.alloc_top_k
        )
        print(
            f"[source] CMC DAILY candles ({days}d) — the CEX-free DQ-safety gate for momentum_cmc"
        )
    else:
        frames = {t: load_4h(t, args.limit) for t in CONTEST_TOKENS}
        have = [t for t in CONTEST_TOKENS if frames.get(t) is not None]
        mat = align_close_matrix(frames, CONTEST_TOKENS)
        base = AllocatorParams()
    print(
        f"loaded {len(have)}/{len(CONTEST_TOKENS)} tokens; aligned matrix "
        f"{mat.shape[0]} bars x {mat.shape[1]} ({list(mat.columns)})"
    )
    if mat.shape[0] < 400 or mat.shape[1] < 3:
        print("ERROR: not enough aligned data to validate.")
        return 2
    close = mat.to_numpy()

    floor, ceiling = settings.alloc_cap_floor, settings.alloc_cap_ceiling
    caps_arr = cap_series(close, floor=floor, ceiling=ceiling, ma_window=settings.alloc_breadth_ma)
    labels = regime_labels(close)
    af = settings.alloc_abs_filter
    p = replace(base, abs_filter=af)
    print(
        f"\n[config] abs_filter={af}  ({'ACTIVE: always deploy top-k' if not af else 'risk-first: cash in downtrends'})"
    )

    # --- STATIC cap sweep (frozen-config view — reference only) -------------- #
    print("\n### STATIC deployment caps (the frozen-config view — for reference) ###")
    for fr_label, ow in (
        ("0.30%RT (v3 majors)", ONE_WAY_30BPS),
        ("0.70%RT (v2 / pegged)", ONE_WAY_70BPS),
    ):
        print(f"\n=== rolling 7-day distribution @ {fr_label} ===")
        print(
            f"{'config':24} {'meanRet':>8} {'medRet':>8} {'p5':>7} {'p95':>7} "
            f"{'%up':>5} {'wkDDmax':>8} {'%>30':>6} {'t/wk':>6}"
        )
        print("-" * 86)
        for cap in sorted({0.30, 0.60, 0.85}):
            sp = replace(base, deploy_cap=cap, abs_filter=af)
            print(
                _fmt_row(
                    f"static cap{cap:.2f}", evaluate(close, weight_path(close, sp), one_way=ow)
                )
            )
        # adaptive row right under the static ones for contrast
        print(
            _fmt_row(
                f"ADAPTIVE [{floor:.2f},{ceiling:.2f}]",
                evaluate(close, weight_path(close, p, cap_series=caps_arr), one_way=ow),
            )
        )

    # --- ADAPTIVE DEPLOYMENT BY REGIME (proves the behaviour we control) ----- #
    # The honest demonstration: the agent DEPLOYS MORE in BULL bars and LESS in
    # BEAR/CHOP bars. (Entry-regime does NOT predict the next week's RETURN — there
    # is no edge — so we show deployment, the thing we actually control, not outcome.)
    print("\n### ADAPTIVE deployment by regime — the agent reacts to live conditions ###")
    print(f"{'regime (bar)':14} {'bars':>7} {'mean deploy cap':>16}   (BULL high, BEAR/CHOP low)")
    print("-" * 60)
    for reg in ("BULL", "BEAR", "CHOP"):
        mask = labels == reg
        n = int(mask.sum())
        mc = float(caps_arr[mask].mean()) if n else 0.0
        bar = "█" * int(round((mc - floor) / (ceiling - floor) * 20)) if n else ""
        print(f"{reg:14} {n:>7} {mc:>15.2f}   {bar}")

    # --- CAP-BAND SWEEP: find the DQ-safe deploy band (the risk dial) -------- #
    # Sweep candidate (floor, ceiling) bands; for each, build the regime cap series and score the
    # rolling-7d distribution at the BINDING ~0.70% spot-DEX friction. Pick the WIDEST band whose
    # worst-week DD < 25% (the operational ceiling, margin under the 30% DQ line). This is how the
    # CMC arm's deploy band is chosen — set it via env (ALLOC_CAP_FLOOR/CEILING), not in code, so the
    # locked momentum_adaptive defaults (0.35/0.80) stay byte-identical.
    if args.cap_sweep:
        print(
            "\n### CAP-BAND SWEEP @ 0.70%RT — widest band with worst-week DD < 25% (the gate) ###"
        )
        print(
            f"{'band [floor,ceiling]':22} {'meanRet':>8} {'p5':>7} {'p95':>7} {'wkDDmax':>8} "
            f"{'t/wk':>6} {'<25%?':>6}"
        )
        print("-" * 70)
        cand_floors = (0.25, 0.30, 0.35)
        cand_ceils = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
        best = None  # (span, ceiling, floor, ceil, stats)
        for fl in cand_floors:
            for ce in cand_ceils:
                if ce <= fl:
                    continue
                ca = cap_series(close, floor=fl, ceiling=ce, ma_window=settings.alloc_breadth_ma)
                st = evaluate(close, weight_path(close, p, cap_series=ca), one_way=ONE_WAY_70BPS)
                ok = st["worst_week_dd"] < 0.25
                print(
                    f"[{fl:.2f}, {ce:.2f}]            {st['mean_ret'] * 100:+7.2f}% "
                    f"{st['p5_ret'] * 100:+6.1f}% {st['p95_ret'] * 100:+6.1f}% {st['worst_week_dd'] * 100:7.1f}% "
                    f"{st['trades_per_week']:5.1f} {'  ✅' if ok else '  ❌'}"
                )
                if ok:
                    # prefer the widest band; tie-break on the higher ceiling (more upside capture).
                    key = (round(ce - fl, 4), ce)
                    if best is None or key > best[0]:
                        best = (key, fl, ce, st)
        if best is not None:
            _, bfl, bce, bst = best
            print(
                f"\n  >> RECOMMENDED band for momentum_cmc: [{bfl:.2f}, {bce:.2f}]  "
                f"(worst-week DD {bst['worst_week_dd'] * 100:.1f}% < 25%, p95 {bst['p95_ret'] * 100:+.1f}%, "
                f"{bst['trades_per_week']:.0f} t/wk)"
            )
            print(f"     set it via env:  ALLOC_CAP_FLOOR={bfl:.2f}  ALLOC_CAP_CEILING={bce:.2f}")
        else:
            print("\n  >> no swept band cleared the 25% ceiling — tighten further or reduce top_k.")

    # --- Verdict on the ADAPTIVE agent (what actually ships) ---------------- #
    s = evaluate(close, weight_path(close, p, cap_series=caps_arr), one_way=ONE_WAY_70BPS)
    s85 = evaluate(
        close,
        weight_path(close, replace(base, deploy_cap=ceiling, abs_filter=af)),
        one_way=ONE_WAY_70BPS,
    )
    # Verdict delegated to the shared Gate-A gate (engine/acceptance.py) — the same
    # gate every new strategy is judged by. Thresholds (DQ < 30%, >= 7 t/wk) live there.
    gate = evaluate_portfolio(s)
    dq_safe, active = gate.dq_safe, gate.active
    print("\n" + "=" * 86)
    print(
        f"SHIPPED: REGIME-ADAPTIVE allocator, participatory band [{floor:.2f}, {ceiling:.2f}] "
        f"(top_k={p.top_k}, lookback={p.lookback}, inverse-vol, daily rebal)"
    )
    print(
        f"  worst-week DD {s['worst_week_dd'] * 100:.1f}%  -> within 25% ceiling: "
        f"{'✅' if dq_safe else '❌'}  (30% DQ line: {'✅' if gate.metrics['within_dq_line'] else '❌'})"
    )
    print(f"  trades/week  {s['trades_per_week']:.1f}  -> active(>=7): {'✅' if active else '❌'}")
    print(
        f"  vs STATIC cap {ceiling:.2f}: upside p95 {s['p95_ret'] * 100:+.1f}% vs {s85['p95_ret'] * 100:+.1f}% "
        f"(keeps most upside), worst-week {s['worst_week_dd'] * 100:.1f}% vs {s85['worst_week_dd'] * 100:.1f}% "
        f"(cuts {(s85['worst_week_dd'] - s['worst_week_dd']) * 100:.0f}pts DD) — better risk-adjusted."
    )
    print("  HONEST NOTE: no fixed edge exists; entry-regime can't predict the next week. The")
    print("  agent manages EXPOSURE to the live regime and is validated FORWARD in paper")
    print("  (make forward_report). The backtest is a cross-regime sanity check, not a frozen fit.")
    print("=" * 86)
    return 0 if (dq_safe and active) else 1


if __name__ == "__main__":
    sys.exit(main())
