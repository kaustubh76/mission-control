"""
Multi-timeframe backtest. Fetches enough history on every timeframe so
that at each replayed 1m bar we can slice all four frames up to that
exact moment and re-evaluate the analyzer.

USAGE:
  python -m ictbot.engine.backtest BTC/USDT:USDT
  python -m ictbot.engine.backtest ETH/USDT:USDT --bars 500
  python -m ictbot.engine.backtest SOL/USDT:USDT --bars 200 --verbose

This is a real walk-forward replay: HTF / 15m / 3m / 1m frames are all
re-sliced at each step so bias-alignment is allowed to change as the
window advances. No look-ahead bias.
"""

import argparse
import sys
from collections import Counter

import pandas as pd

from ictbot.data.factory import get_data, get_default_exchange
from ictbot.orchestrator.analyzer import MIN_BARS, evaluate_frames
from ictbot.runtime.sessions import get_sessions
from ictbot.settings import (
    BIAS_TIMEFRAME,
    ENTRY_TIMEFRAME,
    FEE_PER_SIDE,
    FIB_FILTER,
    FIB_LOOKBACK_BARS,
    HTF_TIMEFRAME,
    POI_TIMEFRAME,
    SLIPPAGE_PER_SIDE,
    TRAIL_BREAKEVEN_R,
)

# Minutes per bar for each timeframe we use.
TF_MINUTES = {"4h": 240, "15m": 15, "3m": 3, "1m": 1}


def _bars_needed(tf: str, backtest_bars_1m: int, warmup_per_tf: int) -> int:
    """How many bars of `tf` we need to back-slice across the 1m window.

    Uses ceiling division so the in-window allocation covers fractional
    bars too (e.g. 5000 1m / 240 = 20.83 → 21 4h bars actually fit
    inside the 1m window, not 20). Floor division here was the cause of
    the empirical 93.8 % INSUFFICIENT_DATA reading on a 5000-bar SOL
    backtest — at T_start we'd be 1 HTF bar short of the MIN_BARS
    threshold and stay short for the entire 4-hour warmup-after-start.

    A small `+ 1` buffer protects against the ⌈⌉ boundary case when
    bars_1m is an exact multiple of TF_MINUTES[tf].
    """
    in_window = -(-backtest_bars_1m // TF_MINUTES[tf])  # ceil division
    return in_window + warmup_per_tf + 1


def _resolve_tick_size(pair: str) -> float | None:
    """Best-effort tick-size lookup from the configured exchange.

    Used to fix the §L finding: backtests previously ran with
    `tick_size=None` which falls back to `round(price, 2)` — a ~1%
    jitter at $0.50 XRP and dwarfs the 0.5% POI tolerance. With this
    helper, a Delta backtest of XRP automatically uses the venue's
    real 0.0001 precision.

    Any exception (no network, mocked exchange, unknown symbol) returns
    None so the legacy fallback still works.
    """
    try:
        ex = get_default_exchange()
        return ex.tick_size(pair)
    except Exception:
        return None


def fetch_history(pair: str, bars: int, use_cache: bool = False) -> dict:
    """Pull enough multi-TF history to back-slice across a `bars`-long 1m window.

    Uses pagination — ccxt's per-call 1000-bar cap is no longer a hard
    ceiling. For very large `bars`, expect several API calls per timeframe.

    `use_cache=True` reads/writes `data/cache/binance/<symbol>/<tf>.parquet`
    via `ictbot.data.cache` — fetches once, replays for free thereafter.
    Saves rate-limit budget during multi-run experiments.
    """
    htf_need = _bars_needed("4h", bars, MIN_BARS["htf"])
    bias_need = _bars_needed("15m", bars, MIN_BARS["bias"])
    poi_need = _bars_needed("3m", bars, MIN_BARS["poi"])
    entry_need = bars + MIN_BARS["entry"] + 10

    if not use_cache:
        return {
            "htf": get_data(pair, HTF_TIMEFRAME, htf_need),
            "bias": get_data(pair, BIAS_TIMEFRAME, bias_need),
            "poi": get_data(pair, POI_TIMEFRAME, poi_need),
            "entry": get_data(pair, ENTRY_TIMEFRAME, entry_need),
        }

    from ictbot.data import cache as _cache

    def _load(tf: str, need: int):
        existing = _cache.read("binance", pair, tf)
        if existing is not None and len(existing) >= need:
            return existing.tail(need).reset_index(drop=True)
        fresh = get_data(pair, tf, need)
        _cache.write("binance", pair, tf, fresh)
        return fresh

    return {
        "htf": _load(HTF_TIMEFRAME, htf_need),
        "bias": _load(BIAS_TIMEFRAME, bias_need),
        "poi": _load(POI_TIMEFRAME, poi_need),
        "entry": _load(ENTRY_TIMEFRAME, entry_need),
    }


def run_backtest(
    pair: str,
    bars: int = 500,
    verbose: bool = False,
    *,
    poi_tolerance: float | None = None,
    sl_frac: float = 0.005,
    tp_frac: float = 0.015,
    sl_atr_mult: float | None = None,
    tp_atr_mult: float | None = None,
    require_fvg: bool = False,  # B3: flipped True → False
    invert: bool = False,
    quiet: bool = False,
    history: dict | None = None,
    start_idx: int | None = None,
    end_idx: int | None = None,
    fee_per_side: float = FEE_PER_SIDE,
    slippage_per_side: float = SLIPPAGE_PER_SIDE,
    trail_breakeven_R: float | None = TRAIL_BREAKEVEN_R,
    bias_engine: str | None = None,
    poi_engine: str | None = None,
    mss_mode: str = "swing",  # E2: ICT-canonical default
    mitigation_bars: int | None = None,
    tick_size: float | None = None,
    killzone_required: bool = False,
    skip_in_low_vol: bool = False,
    delta_mode: str = "sign",
    relative_delta_threshold: float = 0.5,
    delta_window: int = 20,
    # Premium/discount filter on the OB (docs/findings_artifact_diff.md).
    # None defaults to settings.FIB_FILTER so `FIB_FILTER=0.5` env var
    # flows through to the WFO without any code-level grid change. The
    # default is resolved inside the function (see below) so test
    # fixtures that monkey-patch settings still see the live value.
    fib_filter: float | None = None,
    fib_lookback_bars: int | None = None,
    # Phase E — HTF/LTF bias-alignment gate. None defaults to
    # settings.REQUIRE_BIAS_ALIGNMENT (default True). Pass an explicit
    # False from WFO/A-B harnesses to reproduce pre-Phase-E numbers.
    require_bias_alignment: bool | None = None,
) -> dict:
    # Resolve None → settings defaults at call time, not import time, so
    # FIB_FILTER=0.5 in the env reaches every replay path uniformly.
    if fib_filter is None:
        fib_filter = FIB_FILTER
    if fib_lookback_bars is None:
        fib_lookback_bars = FIB_LOOKBACK_BARS
    if require_bias_alignment is None:
        from ictbot.settings import REQUIRE_BIAS_ALIGNMENT as _RBA_DEFAULT

        require_bias_alignment = _RBA_DEFAULT
    """Replay 1m bars and evaluate every step. If `history` is provided,
    reuse it instead of re-fetching. If `start_idx`/`end_idx` are provided,
    only replay that 1m index range (used by walk-forward optimization).
    """
    if not quiet:
        print(f"Fetching multi-TF history for {pair} (replay window = {bars} 1m bars)...")

    if history is None:
        history = fetch_history(pair, bars)
    htf_full = history["htf"]
    bias_full = history["bias"]
    poi_full = history["poi"]
    entry_full = history["entry"]

    # Auto-tick: when the caller didn't pass an explicit tick_size, look
    # it up from the configured exchange. Critical for Delta backtests
    # of low-priced assets (XRP/SOL at 0.0001) — the legacy round(p, 2)
    # fallback produces a ~1% rounding jitter that exceeds POI tolerance
    # and silently breaks tap detection.
    if tick_size is None:
        tick_size = _resolve_tick_size(pair)

    # E5 (ROADMAP): session is recomputed per bar from entry_full["time"]
    # so killzone gating reflects the bar's wall-clock, not wall-clock
    # at backtest-run time. Wall-clock-now is fine for the very first
    # eval (when there's no bar yet); each loop iteration overwrites it.
    session = get_sessions()
    counts = Counter()
    near_misses = []
    signals = []

    if start_idx is not None:
        start = max(MIN_BARS["entry"], start_idx)
    else:
        start = max(MIN_BARS["entry"], len(entry_full) - bars)
    end = end_idx if end_idx is not None else len(entry_full)

    if not quiet:
        print(
            f"Replaying {end - start} 1m bars "
            f"(htf={len(htf_full)}, 15m={len(bias_full)}, 3m={len(poi_full)})..."
        )

    # --- Performance: pre-extract sorted time arrays once so the
    # per-bar slice is O(log n) via numpy.searchsorted instead of O(n)
    # via boolean masking. On 50000-bar replays the boolean mask cost
    # is dominant (~7x slowdown vs 20000 bars), turning the sweep
    # quadratic in window size. searchsorted keeps it linear.
    import numpy as np

    htf_times = htf_full["time"].to_numpy()
    bias_times = bias_full["time"].to_numpy()
    poi_times = poi_full["time"].to_numpy()
    entry_times = entry_full["time"].to_numpy()

    # --- Performance: precompute the delta prefix sum.
    # ictbot.indicators.delta.get_delta is O(n) (sums signed volume over
    # the whole supplied window), and the strategy calls it once per
    # non-position bar against entry_window which grows toward n. That's
    # O(n²) over the full replay. By computing the cumulative signed
    # volume once on entry_full, the per-bar delta is O(1) via index
    # lookup. We monkey-patch the strategy module's `get_delta` for the
    # duration of this run so we don't perturb live/test callers.
    e_close = entry_full["close"].to_numpy()
    e_open = entry_full["open"].to_numpy()
    e_vol = entry_full["volume"].to_numpy()
    signed = np.where(e_close > e_open, e_vol, np.where(e_close < e_open, -e_vol, 0.0))
    # delta_prefix[k] = sum of signed[:k] (so delta over rows [0:k] = prefix[k]).
    delta_prefix = np.concatenate([[0.0], np.cumsum(signed)])

    def _fast_delta(df):
        # Strategy passes a fixed-window slice (entry_full.iloc[k-w:k] after
        # audit gap #3); compute the prefix-sum delta over [k-w, k) so the
        # value matches what the slow path would return on the same slice.
        # Backwards-compat: a caller passing the full growing entry_window
        # (length k, starting at 0) still gets prefix[k] - prefix[0] = prefix[k].
        end_k = len(df) + (df.index[0] if len(df) else 0)
        start_k = end_k - len(df)
        return round(float(delta_prefix[end_k] - delta_prefix[start_k]), 2)

    from unittest.mock import patch as _mock_patch

    import ictbot.strategy.ict_pro_max as _strat_mod

    _delta_patch = _mock_patch.object(_strat_mod, "get_delta", _fast_delta)
    _delta_patch.start()

    # F2 (ROADMAP §F2): precompute HTF & LTF SMA bias series once so each
    # bar's bias becomes an O(1) lookup instead of an O(n) rolling().mean()
    # over the growing window. Applied via the same monkey-patch trick as
    # _fast_delta so callers outside the backtest see the original code.
    htf_close = htf_full["close"]
    htf_sma20 = htf_close.rolling(20).mean().to_numpy()
    htf_sma50 = htf_close.rolling(50).mean().to_numpy()
    bias_close = bias_full["close"]
    bias_sma10 = bias_close.rolling(10).mean().to_numpy()
    bias_sma20 = bias_close.rolling(20).mean().to_numpy()

    def _fast_htf_bias(df):
        # Strategy passes htf_window = htf_full.iloc[:k]; the lookup index
        # is k-1 (the last bar in the window).
        idx = len(df) - 1
        if idx < 0:
            return "BEARISH"
        s20 = htf_sma20[idx]
        s50 = htf_sma50[idx]
        return "BULLISH" if s20 > s50 else "BEARISH"

    def _fast_ltf_bias(df):
        idx = len(df) - 1
        if idx < 0:
            return "BEARISH"
        s10 = bias_sma10[idx]
        s20 = bias_sma20[idx]
        return "BULLISH" if s10 > s20 else "BEARISH"

    import ictbot.indicators.bias_sma as _bias_mod

    _htf_patch = _mock_patch.object(_bias_mod, "get_htf_bias", _fast_htf_bias)
    _ltf_patch = _mock_patch.object(_bias_mod, "get_ltf_bias", _fast_ltf_bias)
    # Also patch the re-exports that ict_pro_max imports directly.
    _htf_alias_patch = _mock_patch.object(_strat_mod, "sma_htf_bias", _fast_htf_bias)
    _ltf_alias_patch = _mock_patch.object(_strat_mod, "sma_ltf_bias", _fast_ltf_bias)
    _htf_patch.start()
    _ltf_patch.start()
    _htf_alias_patch.start()
    _ltf_alias_patch.start()

    # Position-aware loop: once a signal fires, no new signals until SL or TP hits.
    active_position: dict | None = None

    for i in range(start, end + 1):
        T = entry_times[i - 1]

        # E5: refresh session at the bar's wall-clock so killzone gating
        # varies through the replay instead of being a constant from
        # the moment run_backtest started.
        try:
            session = get_sessions(at=pd.Timestamp(T).to_pydatetime())
        except (ValueError, OverflowError):
            # Synthetic time arrays (test fixtures) may not convert cleanly;
            # fall back to wall-clock now.
            session = get_sessions()

        # If we have an open position, see if this bar closes it.
        if active_position is not None:
            bar = entry_full.iloc[i - 1]

            # J5 (audit gap #13): on a single bar that touches both the
            # ORIGINAL SL and the BE trigger, the conservative + correct
            # call is "SL fills first" — intra-bar order is unknowable.
            # The old code promoted SL to BE first and then evaluated
            # against the promoted level, converting real losses to
            # break-evens and biasing backtest results rosy by exactly
            # the trail-savings.
            orig_sl_level = active_position.get("orig_sl", active_position["sl"])
            already_be_moved = active_position.get("be_moved", False)

            outcome = None
            if active_position["entry"] == "BUY":
                if not already_be_moved and bar["low"] <= orig_sl_level:
                    # Bar takes out the ORIGINAL SL before any BE promotion
                    # could have triggered. Real loss, no rescue.
                    active_position["sl"] = orig_sl_level
                    outcome = "LOSS"
                elif bar["high"] >= active_position["tp"]:
                    outcome = "WIN"
            else:  # SELL
                if not already_be_moved and bar["high"] >= orig_sl_level:
                    active_position["sl"] = orig_sl_level
                    outcome = "LOSS"
                elif bar["low"] <= active_position["tp"]:
                    outcome = "WIN"

            # No outcome yet → safe to evaluate BE promotion + the
            # (possibly newly-promoted) SL on the SAME bar. This branch
            # only runs when the original SL was NOT touched this bar.
            if outcome is None:
                if trail_breakeven_R and not active_position.get("be_moved"):
                    entry_price = active_position["price"]
                    risk_dist = abs(entry_price - orig_sl_level)
                    trigger = risk_dist * trail_breakeven_R
                    if active_position["entry"] == "BUY":
                        if bar["high"] >= entry_price + trigger:
                            active_position["sl"] = entry_price
                            active_position["be_moved"] = True
                    else:  # SELL
                        if bar["low"] <= entry_price - trigger:
                            active_position["sl"] = entry_price
                            active_position["be_moved"] = True

                # Re-check the post-promotion SL (in case BE was just set).
                if active_position["entry"] == "BUY":
                    if bar["low"] <= active_position["sl"]:
                        outcome = "BE" if active_position.get("be_moved") else "LOSS"
                else:
                    if bar["high"] >= active_position["sl"]:
                        outcome = "BE" if active_position.get("be_moved") else "LOSS"
            if outcome:
                active_position["outcome"] = outcome
                active_position["closed_at"] = T
                # Compute net R including friction.
                # gross_R = +rr on WIN, 0 on BE (break-even close), -1 on LOSS.
                # friction is measured against the ORIGINAL risk distance so
                # comparisons stay consistent when SL has been moved.
                price = active_position["price"]
                orig_risk_pct = abs(price - active_position["orig_sl"]) / price
                friction_pct = 2 * (fee_per_side + slippage_per_side)
                friction_R = friction_pct / orig_risk_pct if orig_risk_pct else 0
                if outcome == "WIN":
                    gross_R = active_position["rr"]
                elif outcome == "BE":
                    gross_R = 0.0
                else:
                    gross_R = -1.0
                active_position["gross_R"] = gross_R
                active_position["friction_R"] = round(friction_R, 4)
                active_position["net_R"] = round(gross_R - friction_R, 4)
                signals.append(active_position)
                active_position = None
            # While a position is open, don't scan for new entries.
            counts["IN POSITION"] += 1
            continue

        # No position — scan for a new entry.
        # `searchsorted(side="right")` returns the insertion index k such
        # that everything in [0:k] is <= T. Equivalent to the old
        # boolean mask `df[df["time"] <= T]` but O(log n) instead of O(n).
        htf_window = htf_full.iloc[: int(np.searchsorted(htf_times, T, side="right"))]
        bias_window = bias_full.iloc[: int(np.searchsorted(bias_times, T, side="right"))]
        poi_window = poi_full.iloc[: int(np.searchsorted(poi_times, T, side="right"))]
        entry_window = entry_full.iloc[:i]

        r = evaluate_frames(
            htf_window,
            bias_window,
            poi_window,
            entry_window,
            session,
            pair=pair,
            poi_tolerance=poi_tolerance,
            sl_frac=sl_frac,
            tp_frac=tp_frac,
            sl_atr_mult=sl_atr_mult,
            tp_atr_mult=tp_atr_mult,
            require_fvg=require_fvg,
            invert=invert,
            bias_engine=bias_engine,
            poi_engine=poi_engine,
            mss_mode=mss_mode,
            mitigation_bars=mitigation_bars,
            tick_size=tick_size,
            killzone_required=killzone_required,
            skip_in_low_vol=skip_in_low_vol,
            delta_mode=delta_mode,
            relative_delta_threshold=relative_delta_threshold,
            delta_window=delta_window,
            fib_filter=fib_filter,
            fib_lookback_bars=fib_lookback_bars,
            require_bias_alignment=require_bias_alignment,
        )
        if r["error"]:
            counts["INSUFFICIENT DATA"] += 1
            continue

        counts[r["entry"]] += 1

        if r["entry"] in ("BUY", "SELL"):
            active_position = {
                "i": i,
                "time": T,
                "price": r["price"],
                "entry": r["entry"],
                "sl": r["sl"],
                "orig_sl": r["sl"],
                "tp": r["tp"],
                "rr": r["rr"],
                "confidence": r["confidence"],
                "htf_bias": r["htf_bias"],
                "ltf_bias": r["ltf_bias"],
                "outcome": "OPEN",
                "closed_at": None,
                "be_moved": False,
            }
        elif r["diagnostics"]["near_miss"]:
            near_misses.append(
                {
                    "i": i,
                    "time": T,
                    "price": r["price"],
                    "closest": r["diagnostics"]["closest_direction"],
                    "blocker": r["diagnostics"]["blockers"][0],
                }
            )

    # Restore the original indicator functions — monkey-patched only
    # for the duration of this run (delta prefix-sum + F2 SMA prefetch).
    _delta_patch.stop()
    _htf_patch.stop()
    _ltf_patch.stop()
    _htf_alias_patch.stop()
    _ltf_alias_patch.stop()

    # Any position still open at the end of the window stays as OPEN.
    if active_position is not None:
        signals.append(active_position)

    scored = signals

    return {
        "pair": pair,
        "bars_scanned": sum(counts.values()),
        "counts": dict(counts),
        "signals": scored,
        "near_misses": near_misses,
        "verbose": verbose,
    }


def _score_signals(signals: list, entry_full: pd.DataFrame) -> list:
    """For each signal, look at subsequent bars and label WIN/LOSS/OPEN."""
    out = []
    for s in signals:
        future = entry_full[entry_full["time"] > s["time"]]
        outcome = "OPEN"
        for _, bar in future.iterrows():
            if s["entry"] == "BUY":
                if bar["low"] <= s["sl"]:
                    outcome = "LOSS"
                    break
                if bar["high"] >= s["tp"]:
                    outcome = "WIN"
                    break
            else:  # SELL
                if bar["high"] >= s["sl"]:
                    outcome = "LOSS"
                    break
                if bar["low"] <= s["tp"]:
                    outcome = "WIN"
                    break
        out.append({**s, "outcome": outcome})
    return out


def print_report(report: dict) -> None:
    n = report["bars_scanned"] or 1
    print()
    print("=" * 72)
    print(f"BACKTEST REPORT — {report['pair']}")
    print("=" * 72)
    print(f"Bars scanned        : {report['bars_scanned']}")
    for k, v in sorted(report["counts"].items()):
        pct = 100.0 * v / n
        print(f"  {k:<20}: {v} ({pct:.1f}%)")

    sigs = report["signals"]
    print(f"\nSignals fired       : {len(sigs)}")
    wins = sum(1 for s in sigs if s["outcome"] == "WIN")
    losses = sum(1 for s in sigs if s["outcome"] == "LOSS")
    bes = sum(1 for s in sigs if s["outcome"] == "BE")
    opens = sum(1 for s in sigs if s["outcome"] == "OPEN")
    closed_sigs = [s for s in sigs if s["outcome"] in ("WIN", "LOSS", "BE")]
    if closed_sigs:
        win_rate = 100.0 * wins / len(closed_sigs)
        gross_total = sum(s.get("gross_R", 0) for s in closed_sigs)
        net_total = sum(s.get("net_R", 0) for s in closed_sigs)
        gross_exp = gross_total / len(closed_sigs)
        net_exp = net_total / len(closed_sigs)
        avg_friction = sum(s.get("friction_R", 0) for s in closed_sigs) / len(closed_sigs)
        be_str = f"   BE   : {bes}" if bes else ""
        print(
            f"  WIN  : {wins}   LOSS : {losses}{be_str}   OPEN : {opens}   "
            f"win-rate (incl BE in denom) : {win_rate:.1f}%"
        )
        print(
            f"  gross expectancy  : {gross_exp:+.3f}R   "
            f"net (after fees+slip) : {net_exp:+.3f}R   "
            f"friction/trade : {avg_friction:.3f}R"
        )
        print(f"  totals (R)        : gross {gross_total:+.2f}   net {net_total:+.2f}")
    elif sigs:
        print(f"  All {opens} signals are still OPEN — no closures in window")

    for s in sigs[-10:]:
        print(
            f"  [{s['time']}] {s['entry']:<4} @ {s['price']:<10} "
            f"SL={s['sl']:<10} TP={s['tp']:<10} → {s['outcome']}"
        )

    print(f"\nNear misses (5/6)   : {len(report['near_misses'])}")
    if report["verbose"]:
        blocker_counts = Counter(nm["blocker"] for nm in report["near_misses"])
        for blocker, cnt in blocker_counts.most_common(8):
            print(f"  ({cnt}x) {blocker}")
    else:
        print("  (run with --verbose to see what's blocking them)")

    if not sigs and not report["near_misses"]:
        print(
            "\nNOTE: 0 signals + 0 near-misses across the entire window. "
            "Strategy is probably too strict for this market regime — "
            "try loosening POI_TAP_TOLERANCE in ictbot.settings."
        )

    print()


def main():
    ap = argparse.ArgumentParser(description="Backtest ICT AI BOT PRO MAX")
    ap.add_argument("pair", help="Symbol, e.g. BTC/USDT:USDT")
    ap.add_argument(
        "--bars", type=int, default=500, help="How many 1m bars to replay (default 500)"
    )
    ap.add_argument(
        "--verbose", action="store_true", help="Show breakdown of what's blocking near-miss bars"
    )
    ap.add_argument(
        "--poi-tol",
        type=float,
        default=None,
        help="POI tap tolerance as fraction (e.g. 0.005 = 0.5%%)",
    )
    ap.add_argument(
        "--sl", type=float, default=0.005, help="Stop-loss distance as fraction (default 0.005)"
    )
    ap.add_argument(
        "--tp", type=float, default=0.015, help="Take-profit distance as fraction (default 0.015)"
    )
    # Audit gap #6: defaults must match the library defaults. require_fvg
    # library default is False (B3); --require-fvg is the opt-in flag.
    ap.add_argument(
        "--require-fvg",
        action="store_true",
        help="Require a micro FVG for entry (opt-in; library default is False per B3)",
    )
    ap.add_argument(
        "--sl-atr", type=float, default=None, help="SL = N x ATR(14) on 1m (overrides --sl)"
    )
    ap.add_argument(
        "--tp-atr", type=float, default=None, help="TP = N x ATR(14) on 1m (overrides --tp)"
    )
    ap.add_argument(
        "--invert",
        action="store_true",
        help="DIAGNOSTIC: flip every BUY/SELL. If a strategy loses "
        "consistently, inversion should win consistently.",
    )
    ap.add_argument(
        "--trail-be",
        type=float,
        default=None,
        help="Move SL to break-even once price moves N R in favor "
        "(e.g. 1.0 = trail after 1 R of profit)",
    )
    ap.add_argument(
        "--mss-mode",
        choices=["simple", "swing"],
        default="swing",
        help="MSS rule (default: swing — matches library default per E2)",
    )
    ap.add_argument(
        "--mitigation-bars",
        type=int,
        default=None,
        help="Retire a POI N bars after first tap (default: never retire)",
    )
    ap.add_argument(
        "--tick-size",
        type=float,
        default=None,
        help="Tick size for SL/TP rounding. Default: legacy round(p, 2).",
    )
    args = ap.parse_args()

    try:
        report = run_backtest(
            args.pair,
            bars=args.bars,
            verbose=args.verbose,
            poi_tolerance=args.poi_tol,
            sl_frac=args.sl,
            tp_frac=args.tp,
            sl_atr_mult=args.sl_atr,
            tp_atr_mult=args.tp_atr,
            require_fvg=args.require_fvg,
            invert=args.invert,
            trail_breakeven_R=args.trail_be,
            mss_mode=args.mss_mode,
            mitigation_bars=args.mitigation_bars,
            tick_size=args.tick_size,
        )
    except Exception as e:
        print(f"BACKTEST ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print_report(report)


if __name__ == "__main__":
    main()
