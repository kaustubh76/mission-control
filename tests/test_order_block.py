"""
Tests for ict.order_block.
"""

import pandas as pd

from ictbot.indicators.poi_order_block import find_order_block, get_ob_poi


def _df_from_rows(rows):
    """rows = list of (open, high, low, close)."""
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1,
            }
            for i, (o, h, l, c) in enumerate(rows)
        ]
    )


def test_finds_demand_ob_before_a_bullish_swing_low_reversal():
    # Pattern: drop, bottom (swing low), then strong rise.
    # The last RED candle before the swing low becomes the demand OB.
    rows = [
        (110, 111, 109, 110),  # 0
        (110, 110, 108, 108),  # 1 — RED candle (close < open)
        (108, 109, 105, 105),  # 2 — RED, deeper bottom (swing low candidate)
        (105, 106, 105, 105),  # 3
        (105, 108, 104, 107),  # 4 — green, start of rally
        (107, 112, 106, 111),  # 5 — strong green
        (111, 115, 110, 114),  # 6 — confirms swing low at i=2/3
        (114, 116, 113, 115),  # 7
    ]
    df = _df_from_rows(rows)
    ob = find_order_block(df, "BULLISH", swing_lookback=2)
    assert ob is not None
    assert ob["kind"] == "DEMAND"
    # The OB should be one of the red candles before the swing low — index 1 or 2.
    assert ob["index"] in (1, 2)


def test_finds_supply_ob_before_a_bearish_swing_high_reversal():
    # Need a unique swing high — bar 2 must have strictly higher high than its
    # neighbours within `swing_lookback` on either side.
    rows = [
        (100, 102, 99, 101),  # 0
        (101, 103, 100, 102),  # 1 — green
        (102, 112, 102, 110),  # 2 — green, unique high = 112 (swing high)
        (110, 111, 108, 108),  # 3 — start of drop
        (108, 109, 105, 105),  # 4
        (105, 106, 100, 101),  # 5
        (101, 102, 98, 98),  # 6
        (98, 99, 95, 96),  # 7
    ]
    df = _df_from_rows(rows)
    ob = find_order_block(df, "BEARISH", swing_lookback=2)
    assert ob is not None
    assert ob["kind"] == "SUPPLY"
    assert ob["index"] in (1, 2)  # last green candle before/at the top


def test_get_ob_poi_falls_back_when_no_swings_detected():
    # Flat data — no swings → fallback to recent min/max of last 20.
    df = _df_from_rows([(100, 101, 99, 100) for _ in range(20)])
    poi = get_ob_poi(df, "BULLISH")
    assert poi == 99.0  # the recent low


def test_get_ob_poi_returns_ob_top_for_bullish():
    rows = [
        (110, 111, 109, 110),
        (110, 110.5, 108, 108),  # red candle, high=110.5 → expected OB top
        (108, 109, 105, 105),
        (105, 106, 105, 105),
        (105, 108, 104, 107),
        (107, 112, 106, 111),
        (111, 115, 110, 114),
        (114, 116, 113, 115),
    ]
    df = _df_from_rows(rows)
    poi = get_ob_poi(df, "BULLISH")
    # POI must be the top of one of the bearish candles (high)
    assert poi in (110.5, 109)  # high of bar 1 or 2


def test_handles_short_dataframe():
    df = _df_from_rows([(100, 101, 99, 100), (100, 101, 99, 100)])
    assert find_order_block(df, "BULLISH") is None
    # get_ob_poi still returns a number via fallback
    poi = get_ob_poi(df, "BULLISH")
    assert isinstance(poi, float)
