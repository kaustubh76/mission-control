#!/usr/bin/env python3
"""
Gate-A validation for the trend-following strategy on REAL 4h data.

Pulls 4h OHLCV for the contest BEP-20 universe + BNB (public Binance, no API key;
falls back to the cached Bybit snapshot), runs the strategy-agnostic LONG-ONLY
walk-forward replay at three friction levels, and prints a per-asset Gate-A table
plus the portfolio PASS/FAIL decision.

spot-dex friction is BINDING (the contest fills on PancakeSwap/aggregator spot,
~0.70% round-trip). Spot can only go long, so the replay runs long_only=True.

Decision rule (matches docs/strategy_playbook.md):
  PASS  iff  >= 2 assets return "holds" at SPOT-DEX friction with worst rolling-7d
             drawdown < 15%   AND   the deployable basket produces >= 7 trades/week
             (the contest's min-trade floor).
  else  FAIL -> trim basket to survivors / tighter fee tiers, or loosen entry.

Usage:  python scripts/validate_trend.py [--limit 2000] [--train-frac 0.6]
"""

from __future__ import annotations

import argparse
import sys

from ictbot.data import cache
from ictbot.engine.acceptance import DEFAULT as GATE
from ictbot.engine.acceptance import evaluate_basket
from ictbot.engine.wfo_replay import walk_forward
from ictbot.settings import FEE_PER_SIDE, SLIPPAGE_PER_SIDE

# Contest BEP-20 universe + BNB (the 8 tradeable tokens for THIS hackathon).
# All have Binance USDT spot pairs (→ 4h OHLCV) and BSC liquidity for TWAK swaps.
SYMBOLS = [
    "BNB/USDT:USDT",
    "ETH/USDT:USDT",
    "CAKE/USDT:USDT",
    "LINK/USDT:USDT",
    "UNI/USDT:USDT",
    "AVAX/USDT:USDT",
    "DOT/USDT:USDT",
    "DOGE/USDT:USDT",
]

# (fee_per_side, slippage_per_side). spot-dex is the BINDING case: the contest
# executes on PancakeSwap/aggregator spot, ~0.25%/side + slippage. cex / perp-dex
# are kept only for comparison against the earlier (looser-friction) runs.
FRICTIONS = {
    "cex": (FEE_PER_SIDE, SLIPPAGE_PER_SIDE),      # 0.0005 / 0.0002 = 0.14% round-trip
    "perp-dex": (0.0005, 0.0010),                  # ~0.30% round-trip (on-chain perp taker)
    "spot-dex": (0.0025, 0.0010),                  # ~0.70% round-trip (PancakeSwap v2 taker) — BINDING
}
BINDING = "spot-dex"

# Spot can only go long — the validated edge is the long side of the trend signal.
LONG_ONLY = True

# 7-day live window = 42 four-hour bars. Contest requires >= 7 trades total.
BARS_PER_WEEK = 42
MIN_WEEKLY_TRADES = 7

MIN_BARS = 250
DD_CEIL = 0.15
HOLD = "✅ holds"


def trades_per_week(res: dict) -> float | None:
    """Estimate trades per 7-day (42-bar) window from the TEST closures rate."""
    n_test = res.get("n_test") or 0
    closures = res.get("test_closures") or 0
    if n_test <= 0:
        return None
    return closures * BARS_PER_WEEK / n_test


def load_4h(sym: str, limit: int):
    """Return (df, source) — try live Binance, then binance cache, then bybit cache."""
    try:
        from ictbot.data.binance import BinanceExchange

        df = BinanceExchange().fetch_ohlcv(sym, "4h", limit)
        if df is not None and len(df) >= MIN_BARS:
            cache.write("binance", sym, "4h", df)
            return df.tail(limit).reset_index(drop=True), "binance(live)"
    except Exception:
        pass
    try:
        df = cache.read("binance", sym, "4h")
        if df is not None and len(df) >= MIN_BARS:
            return df.tail(limit).reset_index(drop=True), "binance(cache)"
    except Exception:
        pass
    try:
        from ictbot.data.replay import ReplayExchange

        df = ReplayExchange(exchange="bybit").fetch_ohlcv(sym, "4h", limit)
        if df is not None and len(df) >= MIN_BARS:
            return df, "bybit(cache)"
    except Exception:
        pass
    return None, "no-data"


def _fmt(x):
    return "  n/a" if x is None else f"{x:+.3f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--train-frac", type=float, default=0.6)
    args = ap.parse_args()

    # Load data once per symbol (reused across friction levels).
    data: dict[str, tuple] = {}
    for sym in SYMBOLS:
        data[sym] = load_4h(sym, args.limit)

    bind_results: dict[str, dict] = {}
    for label, (fee, slip) in FRICTIONS.items():
        print(f"\n=== Gate A @ {label} friction "
              f"(fee/side {fee:.4f}, slip/side {slip:.4f}, "
              f"round-trip {2 * (fee + slip) * 100:.2f}%, long_only={LONG_ONLY}) ===")
        print(f"{'pair':16} {'source':16} {'bars':>5} {'verdict':12} "
              f"{'TRAIN':>7} {'TEST':>7} {'clos':>5} {'7dDD':>7} {'t/wk':>6}")
        print("-" * 94)
        for sym in SYMBOLS:
            df, src = data[sym]
            if df is None:
                print(f"{sym:16} {src:16} {'-':>5} {'(skipped)':12}")
                continue
            res = walk_forward(
                df, train_frac=args.train_frac,
                fee_per_side=fee, slippage_per_side=slip,
                long_only=LONG_ONLY,
            )
            if label == BINDING:
                bind_results[sym] = res
            dd = res.get("worst_7d_dd")
            tpw = trades_per_week(res)
            print(f"{sym:16} {src:16} {len(df):>5} {res['verdict']:12} "
                  f"{_fmt(res.get('train_exp')):>7} {_fmt(res.get('test_exp')):>7} "
                  f"{res.get('test_closures', 0):>5} "
                  f"{('  n/a' if dd is None else f'{dd * 100:5.1f}%'):>7} "
                  f"{('  n/a' if tpw is None else f'{tpw:5.2f}'):>6}")

    # Portfolio decision (spot-dex friction is binding — that's how the contest fills).
    # `holders` + basket trade-rate are computed here for the printout; the PASS/FAIL
    # thresholds (>= 2 holders, DD < target, >= 7 t/wk) are delegated to the shared
    # Gate-A gate (engine/acceptance.py) so every strategy is judged by one rulebook.
    holders = [
        s for s, r in bind_results.items()
        if r.get("verdict") == HOLD
        and r.get("worst_7d_dd") is not None
        and r["worst_7d_dd"] < GATE.target_worst_week_dd
    ]
    # Weekly trade-rate of the DEPLOYABLE basket = sum over holders (what we'd trade).
    basket_tpw = sum(
        (trades_per_week(bind_results[s]) or 0.0) for s in holders
    )
    gate = evaluate_basket(bind_results, basket_tpw)
    enough_edge, enough_trades, passed = gate.dq_safe, gate.active, gate.passed

    print("\n" + "=" * 94)
    print(f"PORTFOLIO GATE A ({BINDING} friction):  "
          f"{len(holders)} asset(s) hold with 7dDD < {DD_CEIL:.0%}  "
          f"-> {', '.join(holders) if holders else 'none'}")
    print(f"  edge gate:   {'✅' if enough_edge else '❌'}  "
          f"({len(holders)} holders, need >= 2)")
    print(f"  trade gate:  {'✅' if enough_trades else '❌'}  "
          f"(deployable basket ~{basket_tpw:.1f} trades/week, need >= {MIN_WEEKLY_TRADES})")
    if passed:
        print("VERDICT: ✅ PASS  -> ship spot long-only 1x trend via TWAK on this basket.")
    else:
        print("VERDICT: ❌ FAIL  -> the spot long-only trend edge did NOT clear Gate A on")
        print("         the contest universe at realistic DEX friction. Trim the basket to")
        print("         survivors + tighter (v3) fee tiers, or loosen the entry to lift the")
        print("         trade count. Do NOT deploy unproven.")
    print("=" * 94)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
