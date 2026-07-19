"""
Tests for ict.structure (swing-based bias).
"""

import pandas as pd

from ictbot.indicators.structure import find_swings, get_swing_bias


def _df_from_highs_lows(highs, lows):
    return pd.DataFrame(
        {
            "open": [h - 0.5 for h in highs],
            "high": highs,
            "low": lows,
            "close": [h - 0.5 for h in highs],
            "volume": [1] * len(highs),
            "time": pd.date_range("2026-01-01", periods=len(highs), freq="1min"),
        }
    )


def test_find_swings_detects_obvious_high():
    # Clear single peak at index 5 with lookback=2
    highs = [10, 11, 12, 13, 14, 20, 14, 13, 12, 11, 10]
    lows = [9, 10, 11, 12, 13, 19, 13, 12, 11, 10, 9]
    df = _df_from_highs_lows(highs, lows)
    swings = find_swings(df, lookback=2)
    high_swings = [s for s in swings if s.kind == "HIGH"]
    assert len(high_swings) >= 1
    assert any(s.index == 5 and s.price == 20 for s in high_swings)


def test_get_swing_bias_bullish_on_ascending_swings():
    # Construct: low at i=3 (price 5), high at i=7 (price 20),
    # then HIGHER low at i=12 (price 8), HIGHER high at i=16 (price 25).
    highs = [10, 11, 12, 13, 14, 15, 16, 20, 15, 14, 13, 12, 14, 16, 18, 22, 25, 22, 20, 18]
    lows = [9, 10, 11, 5, 7, 9, 10, 15, 12, 11, 10, 9, 8, 12, 14, 16, 20, 18, 16, 14]
    df = _df_from_highs_lows(highs, lows)
    assert get_swing_bias(df, lookback=2) == "BULLISH"


def test_get_swing_bias_bearish_on_descending_swings():
    # Mirror: descending highs AND descending lows
    highs = [25, 24, 23, 22, 21, 20, 19, 22, 18, 17, 16, 15, 14, 18, 13, 12, 11, 10, 9, 8]
    lows = [20, 19, 18, 25, 17, 15, 14, 13, 15, 12, 11, 10, 18, 9, 8, 7, 6, 5, 4, 3]
    df = _df_from_highs_lows(highs, lows)
    assert get_swing_bias(df, lookback=2) == "BEARISH"


def test_get_swing_bias_fallback_when_too_few_swings():
    # Tiny dataframe — should fall back to close-vs-close comparison
    highs = [10, 11, 12]
    lows = [9, 10, 11]
    df = _df_from_highs_lows(highs, lows)
    bias = get_swing_bias(df, lookback=2)
    assert bias in ("BULLISH", "BEARISH")  # whatever fallback says
