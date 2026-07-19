"""
Market Structure Shift (MSS) on the 1m entry timeframe.

Two modes:

  - "simple" (legacy, default): last high > prev high (BULLISH) / last low
    < prev low (BEARISH). One bar of confirmation — fast, noisy.

  - "swing": the close of the latest bar must break above the most recent
    protected swing high (BULLISH) or below the most recent swing low
    (BEARISH). This is the textbook ICT definition. Requires `lookback`
    bars on either side of the swing pivot to count it as protected, so
    needs a longer history than "simple".
"""

from __future__ import annotations

import pandas as pd

from ictbot.indicators.structure import find_swings


def get_ltf_mss(df: pd.DataFrame, bias: str, mode: str = "simple", lookback: int = 3) -> str:
    """Return 'BULLISH MSS' / 'BEARISH MSS' / 'NO MSS'.

    `mode="simple"` is the legacy 2-bar rule kept for backwards compat
    (every existing test uses it implicitly).
    """
    if mode == "swing":
        return _swing_mss(df, bias, lookback=lookback)

    last_high, prev_high = df["high"].iloc[-1], df["high"].iloc[-2]
    last_low, prev_low = df["low"].iloc[-1], df["low"].iloc[-2]

    if bias == "BULLISH" and last_high > prev_high:
        return "BULLISH MSS"
    if bias == "BEARISH" and last_low < prev_low:
        return "BEARISH MSS"
    return "NO MSS"


def _swing_mss(df: pd.DataFrame, bias: str, lookback: int = 3) -> str:
    """Real ICT MSS: close breaks the most recent protected swing pivot."""
    if len(df) < lookback * 2 + 2:
        return "NO MSS"

    swings = find_swings(df, lookback=lookback)
    if not swings:
        return "NO MSS"

    last_close = float(df["close"].iloc[-1])

    if bias == "BULLISH":
        # The most recent swing HIGH that is *not* the current bar.
        highs = [s for s in swings if s.kind == "HIGH" and s.index < len(df) - 1]
        if not highs:
            return "NO MSS"
        target = highs[-1].price
        return "BULLISH MSS" if last_close > target else "NO MSS"

    if bias == "BEARISH":
        lows = [s for s in swings if s.kind == "LOW" and s.index < len(df) - 1]
        if not lows:
            return "NO MSS"
        target = lows[-1].price
        return "BEARISH MSS" if last_close < target else "NO MSS"

    return "NO MSS"


def get_ltf_mss_time(df: pd.DataFrame, bias: str, mode: str = "simple", lookback: int = 3):
    """Return the timestamp of the bar where MSS confirmed, or None.

    Both `simple` and `swing` modes confirm the break on `df.iloc[-1]`,
    so this is just the last bar's `time` when get_ltf_mss returns a
    BULLISH/BEARISH label. Exists separately so the strategy can ask
    "did MSS happen?" and "when?" without coupling the FVG-after-MSS
    gate to MSS internals.
    """
    label = get_ltf_mss(df, bias, mode=mode, lookback=lookback)
    if label == "NO MSS":
        return None
    # iloc[-1] is the confirming bar in both modes today; if a future
    # MSS mode resolves on a different bar, return that bar's `time`
    # here instead of the last row.
    try:
        return df["time"].iloc[-1]
    except (KeyError, IndexError):
        return None
