"""
Strategy-agnostic walk-forward replay (4h-native).

The existing run_backtest / engine.wfo are a 1m-entry, ICT-parameter-bound loop
(`_iter_combos` unpacks `(poi_tol, sl, tp, fvg)`, `evaluate_frames` hard-builds
ICTProMaxStrategy) — they cannot validate a generic 4h trend signal. This module
is the ~150-LOC agnostic replay the strategy switch needs: it drives ANY per-bar
signal function over single-timeframe OHLCV, applies the SAME friction model as
run_backtest (`friction_R = 2*(fee+slippage)/orig_risk_pct`), and scores the
walk-forward TRAIN/TEST split through the SAME Gate-A verdict
(`ictbot.engine.wfo.classify`).

It is deliberately decoupled from the ICT stack so the trend signal is judged on
its own merits, on real 4h data, before any leveraged execution is built.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ictbot.engine.wfo import classify  # reuse the exact Gate-A verdict
from ictbot.settings import FEE_PER_SIDE, SLIPPAGE_PER_SIDE
from ictbot.strategy.trend_basket import (
    WARMUP,
    TrendParams,
    base_features,
    compute_features,
    signal_at,
)

BARS_PER_DAY_4H = 6
ROLL_7D_BARS = 7 * BARS_PER_DAY_4H  # 42 four-hour bars
MIN_TRAIN_TRADES = 5  # don't trust a TRAIN winner on fewer
RISK_PCT = 0.01  # equity fraction per trade (for the DD curve)


@dataclass
class Trade:
    entry_i: int
    close_i: int
    side: str
    gross_R: float
    friction_R: float
    net_R: float
    outcome: str  # WIN | LOSS | BE


def replay(
    df: pd.DataFrame,
    p: TrendParams,
    *,
    fee_per_side: float = FEE_PER_SIDE,
    slippage_per_side: float = SLIPPAGE_PER_SIDE,
    trail_be_R: float | None = 1.0,
    start: int = WARMUP,
    end: int | None = None,
    base=None,
) -> list[Trade]:
    """Replay one param set over df[start:end], one position at a time."""
    n = len(df)
    end = (n - 1) if end is None else min(end, n - 1)
    feat = compute_features(df, p, base=base)
    high = feat.base.high
    low = feat.base.low
    friction_const = 2.0 * (fee_per_side + slippage_per_side)

    trades: list[Trade] = []
    i = max(start, WARMUP)
    while i <= end:
        sig = signal_at(feat, i)
        if sig is None:
            i += 1
            continue
        side = sig["side"]
        entry = sig["price"]
        orig_sl = sig["sl"]
        tp = sig["tp"]
        rr = sig["rr"]
        risk = abs(entry - orig_sl)
        be_level = entry + risk if side == "BUY" else entry - risk
        be_moved = False

        outcome = None
        close_i = None
        for j in range(i + 1, end + 1):
            hi, lo = high[j], low[j]
            sl_level = entry if be_moved else orig_sl
            if side == "BUY":
                if lo <= sl_level:  # stop first (pessimistic)
                    outcome = "BE" if be_moved else "LOSS"
                elif hi >= tp:
                    outcome = "WIN"
                elif trail_be_R is not None and hi >= be_level:
                    be_moved = True
            else:  # SELL
                if hi >= sl_level:
                    outcome = "BE" if be_moved else "LOSS"
                elif lo <= tp:
                    outcome = "WIN"
                elif trail_be_R is not None and lo <= be_level:
                    be_moved = True
            if outcome is not None:
                close_i = j
                break

        if outcome is None:  # ran off the end still open — not a closed trade
            break

        gross_R = rr if outcome == "WIN" else (0.0 if outcome == "BE" else -1.0)
        orig_risk_pct = risk / entry if entry else 0.0
        friction_R = friction_const / orig_risk_pct if orig_risk_pct else 0.0
        trades.append(
            Trade(
                entry_i=i,
                close_i=close_i,
                side=side,
                gross_R=round(gross_R, 4),
                friction_R=round(friction_R, 4),
                net_R=round(gross_R - friction_R, 4),
                outcome=outcome,
            )
        )
        i = close_i + 1  # resume scanning after the close

    return trades


def expectancy(trades: list[Trade]) -> tuple[float | None, int]:
    closed = [t for t in trades if t.outcome in ("WIN", "LOSS", "BE")]
    if not closed:
        return None, 0
    return float(np.mean([t.net_R for t in closed])), len(closed)


def equity_drawdown(
    trades: list[Trade], start: int, end: int, risk_pct: float = RISK_PCT
) -> tuple[float, float]:
    """Return (max_dd, worst_rolling_7d_dd) as positive fractions on the equity curve."""
    eq_at_close: dict[int, float] = {}
    eq = 1.0
    for t in sorted(trades, key=lambda x: x.close_i):
        eq *= 1.0 + risk_pct * t.net_R
        eq_at_close[t.close_i] = eq
    # per-bar equity (carry the last close forward)
    series = []
    cur = 1.0
    for b in range(start, end + 1):
        if b in eq_at_close:
            cur = eq_at_close[b]
        series.append(cur)
    arr = np.asarray(series, dtype=float)
    if arr.size == 0:
        return 0.0, 0.0
    peak = np.maximum.accumulate(arr)
    max_dd = float(np.max((peak - arr) / peak))
    worst_roll = 0.0
    for j in range(arr.size):
        lo = max(0, j - ROLL_7D_BARS + 1)
        wpeak = float(np.max(arr[lo : j + 1]))
        if wpeak > 0:
            worst_roll = max(worst_roll, (wpeak - arr[j]) / wpeak)
    return max_dd, worst_roll


# --------------------------------------------------------------------------- #
# Parameter grid + walk-forward
# --------------------------------------------------------------------------- #
def grid_trend(long_only: bool = False) -> list[TrendParams]:
    """RR>=2-enforced trend grid (~32 combos)."""
    combos = []
    for ma_window, slope_period, sl_atr, rr, pb in itertools.product(
        (20, 50), (20, 40), (1.5, 2.0), (2.0, 3.0), (3, 5)
    ):
        combos.append(
            TrendParams(
                ma_window=ma_window,
                slope_period=slope_period,
                sl_atr=sl_atr,
                rr=rr,
                pullback_lookback=pb,
                long_only=long_only,
                allow_short=not long_only,
            )
        )
    return combos


def walk_forward(
    df: pd.DataFrame,
    *,
    train_frac: float = 0.6,
    fee_per_side: float = FEE_PER_SIDE,
    slippage_per_side: float = SLIPPAGE_PER_SIDE,
    long_only: bool = False,
    min_closures: int = 10,
    grid: list[TrendParams] | None = None,
) -> dict:
    """Sweep the grid on TRAIN, re-evaluate the winner on TEST, classify the edge."""
    n = len(df)
    if n < WARMUP + 60:
        return {"verdict": "no data", "n_bars": n}

    total = (n - 1) - WARMUP
    split = WARMUP + int(total * train_frac)
    base = base_features(df)  # fixed features computed once, shared across combos
    grid = grid or grid_trend(long_only)

    best = None  # (train_exp, n_train, params)
    for p in grid:
        tr = replay(
            df,
            p,
            fee_per_side=fee_per_side,
            slippage_per_side=slippage_per_side,
            start=WARMUP,
            end=split,
            base=base,
        )
        exp, n_closed = expectancy(tr)
        if exp is None or n_closed < MIN_TRAIN_TRADES:
            continue
        if best is None or exp > best[0]:
            best = (exp, n_closed, p)

    if best is None:
        return {
            "verdict": classify(None, None),  # "no edge"
            "train_exp": None,
            "test_exp": None,
            "test_closures": 0,
            "worst_7d_dd": None,
            "best_params": None,
            "n_train": split - WARMUP,
            "n_test": (n - 1) - split,
        }

    train_exp, n_train, p = best
    test = replay(
        df,
        p,
        fee_per_side=fee_per_side,
        slippage_per_side=slippage_per_side,
        start=split + 1,
        end=n - 1,
        base=base,
    )
    test_exp, test_closures = expectancy(test)
    max_dd, worst_7d = equity_drawdown(test, split + 1, n - 1)
    verdict = classify(train_exp, test_exp, test_closures, min_closures=min_closures)
    return {
        "verdict": verdict,
        "train_exp": round(train_exp, 4),
        "train_closures": n_train,
        "test_exp": round(test_exp, 4) if test_exp is not None else None,
        "test_closures": test_closures,
        "max_dd": round(max_dd, 4),
        "worst_7d_dd": round(worst_7d, 4),
        "best_params": p,
        "n_train": split - WARMUP,
        "n_test": (n - 1) - split,
    }
