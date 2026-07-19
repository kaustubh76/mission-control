"""
Price channels — Donchian (rolling high/low) and Keltner (EMA ± ATR).

Used by the volatility-breakout strategy (a Donchian breakout re-expressed as a
target-weight book). Two shapes per indicator, matching the house convention:
  - `get_*(df) -> latest scalar(s)`   (live, DataFrame in)
  - `*_series(np.ndarray) -> full (n,) series`  (vectorised backtest, numpy in)

CAUSALITY: the Donchian channel is `.shift(1)` — it EXCLUDES the current bar, so a
breakout test `close[i] > upper[i]` compares the bar against the PRIOR `period` bars
(not a window containing itself, which would be degenerate/lookahead).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy.trend_basket import _atr_series


def donchian_upper_series(high: np.ndarray, period: int = 20) -> np.ndarray:
    """Highest high over the prior `period` bars (excludes the current bar)."""
    return pd.Series(high).rolling(period).max().shift(1).to_numpy()


def donchian_lower_series(low: np.ndarray, period: int = 20) -> np.ndarray:
    """Lowest low over the prior `period` bars (excludes the current bar)."""
    return pd.Series(low).rolling(period).min().shift(1).to_numpy()


def get_donchian(df: pd.DataFrame, period: int = 20) -> tuple[float, float]:
    """Latest (upper, lower) Donchian channel from df['high']/df['low'].

    Returns (nan, nan) when there is not enough history (< period + 1 bars)."""
    if len(df) < period + 1:
        return (float("nan"), float("nan"))
    up = donchian_upper_series(df["high"].to_numpy(dtype=float), period)
    dn = donchian_lower_series(df["low"].to_numpy(dtype=float), period)
    return (float(up[-1]), float(dn[-1]))


def _ema_series(x: np.ndarray, span: int) -> np.ndarray:
    """Causal EMA (adjust=False) — same convention as strategy.technicals.ema."""
    return pd.Series(x).ewm(span=span, adjust=False, min_periods=1).mean().to_numpy()


def keltner_series(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 20,
    mult: float = 2.0,
    atr_period: int = 14,
) -> tuple[np.ndarray, np.ndarray]:
    """Keltner band (upper, lower): EMA(close, period) ± mult * ATR(atr_period).

    Reuses `trend_basket._atr_series` for the ATR (no re-implementation of TR)."""
    mid = _ema_series(close, period)
    atr = _atr_series(high, low, close, atr_period)
    return mid + mult * atr, mid - mult * atr


def get_keltner(
    df: pd.DataFrame, period: int = 20, mult: float = 2.0, atr_period: int = 14
) -> tuple[float, float]:
    """Latest (upper, lower) Keltner band; (nan, nan) on insufficient history."""
    if len(df) < max(period, atr_period) + 1:
        return (float("nan"), float("nan"))
    up, dn = keltner_series(
        df["high"].to_numpy(dtype=float),
        df["low"].to_numpy(dtype=float),
        df["close"].to_numpy(dtype=float),
        period,
        mult,
        atr_period,
    )
    return (float(up[-1]), float(dn[-1]))
