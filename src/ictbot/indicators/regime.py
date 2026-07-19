"""
Simple regime classification: trending vs ranging vs high-vol.

Phase 9 may swap in something more sophisticated (ADX, Hurst, fractal
dimension). For now ATR percentile is the cheapest signal that
correlates with what a discretionary trader would call "regime":

  - HIGH_VOL : current ATR ranks above `hi` percentile of recent history.
  - LOW_VOL  : below `lo` percentile.
  - NORMAL   : in between.

Strategy can use this to widen stops in HIGH_VOL, skip entries in
LOW_VOL, or just record the regime alongside each signal.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from ictbot.indicators.atr import get_atr

Regime = Literal["HIGH_VOL", "LOW_VOL", "NORMAL"]


def atr_percentile_regime(
    df: pd.DataFrame,
    period: int = 14,
    window: int = 200,
    hi: float = 0.70,
    lo: float = 0.30,
) -> Regime:
    """Return the regime label for the current bar.

    Falls back to "NORMAL" when there isn't enough history to rank.
    """
    if len(df) < period + window:
        return "NORMAL"

    # True-range series mirrors ictbot.indicators.atr.
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_series = tr.rolling(period).mean()
    recent = atr_series.tail(window).dropna()
    if recent.empty:
        return "NORMAL"
    current = float(get_atr(df, period=period))
    if current <= 0:
        return "NORMAL"
    rank = float((recent <= current).mean())  # ECDF
    if rank >= hi:
        return "HIGH_VOL"
    if rank <= lo:
        return "LOW_VOL"
    return "NORMAL"
