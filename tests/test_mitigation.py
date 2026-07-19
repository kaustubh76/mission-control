"""Tests for shared mitigation helper (Phase 6 / gaps S3/S4/S5)."""

import pandas as pd

from ictbot.indicators.mitigation import first_tap_index, is_mitigated


def _df(lows, highs):
    n = len(lows)
    return pd.DataFrame(
        {
            "time": pd.to_datetime([i * 60_000 for i in range(n)], unit="ms"),
            "open": [100.0] * n,
            "close": [100.0] * n,
            "low": lows,
            "high": highs,
            "volume": [10] * n,
        }
    )


def test_first_tap_demand_returns_first_low_breach():
    df = _df(
        lows=[99, 99, 95, 99, 95],  # first tap of level=95 at index 2
        highs=[101] * 5,
    )
    assert first_tap_index(df, level=95, side="demand") == 2


def test_first_tap_supply_returns_first_high_breach():
    df = _df(
        lows=[99] * 5,
        highs=[101, 101, 110, 105, 115],  # first tap of level=110 at index 2
    )
    assert first_tap_index(df, level=110, side="supply") == 2


def test_first_tap_returns_none_when_never_tagged():
    df = _df(lows=[99] * 5, highs=[101] * 5)
    assert first_tap_index(df, level=50, side="demand") is None
    assert first_tap_index(df, level=200, side="supply") is None


def test_mitigation_after_retire_window_elapses():
    df = _df(lows=[99, 99, 95] + [99] * 7, highs=[101] * 10)  # tap at idx 2
    # bars_since_tap on the last bar = 9 - 2 = 7
    assert is_mitigated(df, level=95, side="demand", retire_bars=5) is True
    assert is_mitigated(df, level=95, side="demand", retire_bars=10) is False


def test_mitigation_zero_retire_disables_check():
    df = _df(lows=[99, 95, 95, 95], highs=[101] * 4)
    assert is_mitigated(df, level=95, side="demand", retire_bars=0) is False
