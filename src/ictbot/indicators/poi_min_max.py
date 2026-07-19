"""
Point of Interest (POI) on the 3m timeframe.

BULLISH bias: POI is the recent swing low (potential demand zone).
BEARISH bias: POI is the recent swing high (potential supply zone).
"""

import pandas as pd

from ictbot.indicators.tick import round_to_tick
from ictbot.settings import POI_TAP_TOLERANCE


def get_ltf_poi(
    df: pd.DataFrame,
    bias: str,
    lookback: int = 20,
    tick_size: float | None = None,
) -> float:
    """Audit gap #4: legacy round(price, 2) is a 1 % jitter on a $0.50
    asset like XRP — larger than POI_TAP_TOLERANCE. POI taps on
    low-priced assets were randomly mis-detected. tick_size threads
    the exchange's precision through so rounding matches reality.
    """
    if bias == "BULLISH":
        raw = float(df["low"].tail(lookback).min())
    else:
        raw = float(df["high"].tail(lookback).max())
    return round_to_tick(raw, tick_size)


def get_poi_tap(df: pd.DataFrame, poi: float, tolerance_frac: float | None = None) -> str:
    """Return 'POI TAPPED' if current price is within tolerance of the POI.

    tolerance_frac defaults to ictbot.settings.POI_TAP_TOLERANCE but can be
    overridden (used by the parameter sweep).
    """
    current_price = df["close"].iloc[-1]
    tol = POI_TAP_TOLERANCE if tolerance_frac is None else tolerance_frac
    tolerance = current_price * tol
    if abs(current_price - poi) <= tolerance:
        return "POI TAPPED"
    return "WAITING"
