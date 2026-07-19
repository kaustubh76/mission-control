"""
E3 (ROADMAP §E3) — FVG mitigation gate.

A filled FVG is no longer an imbalance. With `mitigation_bars=N`, an
FVG that gets filled within N bars of formation must report NO FVG.
"""

import pandas as pd

from ictbot.indicators.fvg import get_micro_fvg


def _df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build a 4-column OHLC DataFrame from (open, high, low, close) tuples."""
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        }
    )


def _bullish_fvg_rows():
    """6-bar window with a bullish FVG between bar 0 (high=100) and
    bar 2 (low=110). Gap = (100, 110).

    Bars 3-5 stay ABOVE the gap (lows > 110) so the gap is not filled,
    AND low[5] <= high[3] so the legacy last-3-bar check doesn't find
    a fresh FVG between bars 3 and 5. That makes this fixture useful
    for the "older_unfilled_fvg" scenario.
    """
    return [
        (98, 100, 97, 99),  # bar 0: high=100
        (99, 105, 99, 104),  # bar 1: middle candle
        (105, 112, 110, 111),  # bar 2: low=110 > high[0]=100 → bullish FVG
        (111, 115, 111, 113),  # bar 3: high=115, low=111 (above gap)
        (113, 120, 112, 118),  # bar 4: range above gap
        (118, 119, 113, 114),  # bar 5: low=113 <= high[3]=115 → no legacy FVG
    ]


def test_legacy_no_mitigation_returns_fvg_on_last_three_bars():
    # Need >= 5 bars for get_micro_fvg to evaluate at all.
    df = _df(
        [
            (98, 99, 97, 98),
            (98, 100, 97, 99),
            (99, 102, 99, 101),  # high[-3]=102
            (101, 105, 101, 104),  # middle
            (104, 110, 106, 108),  # low[-1]=106 > high[-3]=102 → BULLISH FVG
        ]
    )
    assert get_micro_fvg(df, "BULLISH") == "BULLISH FVG"


def test_legacy_no_mitigation_returns_no_fvg_when_gap_absent():
    df = _df(
        [
            (100, 102, 99, 101),
            (101, 103, 100, 102),
            (102, 105, 99, 101),
            (101, 103, 100, 102),
            (102, 104, 100, 103),
        ]
    )
    assert get_micro_fvg(df, "BULLISH") == "NO FVG"


def test_mitigation_keeps_fvg_when_gap_not_filled():
    df = _df(_bullish_fvg_rows())
    # No subsequent bar dips back below high[0]=100 → gap intact.
    assert get_micro_fvg(df, "BULLISH", mitigation_bars=10) == "BULLISH FVG"


def test_mitigation_retires_fvg_when_subsequent_bar_fills_gap():
    rows = _bullish_fvg_rows()
    # Replace bar 4 with one that dips into the gap (low <= high[0]=100).
    # Gap is between high[0]=100 and low[2]=110; any subsequent bar with
    # low <= 100 fills it.
    rows[4] = (115, 115, 95, 105)
    df = _df(rows)
    # The FVG formed at bar 2; bar 4 filled it within mitigation_bars=10.
    assert get_micro_fvg(df, "BULLISH", mitigation_bars=10) == "NO FVG"


def test_mitigation_zero_falls_back_to_legacy_check():
    """mitigation_bars=0 must behave identically to the legacy code path."""
    df = _df(_bullish_fvg_rows())
    # Last 3 bars (3,4,5) have NO gap.
    assert get_micro_fvg(df, "BULLISH", mitigation_bars=0) == "NO FVG"


def test_mitigation_finds_older_unfilled_fvg_when_legacy_misses():
    """The legacy check only inspects the last 3 candles. With mitigation
    we scan further back and can surface an earlier FVG that's still active."""
    df = _df(_bullish_fvg_rows())  # FVG at bar 2; bars 3-5 are mid-gap noise
    # Legacy (mitigation_bars=None) misses it because last-3 = bars 3,4,5.
    assert get_micro_fvg(df, "BULLISH") == "NO FVG"
    # With scan window: surface the bar-2 FVG.
    assert get_micro_fvg(df, "BULLISH", mitigation_bars=10) == "BULLISH FVG"


def test_bearish_fvg_mitigation_path():
    rows = [
        (110, 113, 109, 110),
        (110, 110, 105, 106),
        (106, 106, 100, 101),  # high[2]=106 < low[0]=109 → bearish FVG
        (101, 105, 100, 104),  # mid
        (104, 105, 102, 103),  # mid
        (103, 104, 102, 103),
    ]
    df = _df(rows)
    # No bar's high >= low[0]=109 → unfilled.
    assert get_micro_fvg(df, "BEARISH", mitigation_bars=10) == "BEARISH FVG"

    # Fill it: bar 4's high pokes back to 110 (>= 109).
    rows[4] = (104, 110, 102, 103)
    df_filled = _df(rows)
    assert get_micro_fvg(df_filled, "BEARISH", mitigation_bars=10) == "NO FVG"


def test_short_dataframe_returns_no_fvg():
    df = _df([(100, 101, 99, 100)] * 3)
    assert get_micro_fvg(df, "BULLISH", mitigation_bars=10) == "NO FVG"
