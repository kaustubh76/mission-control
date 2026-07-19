"""Tests for Donchian + Keltner channel primitives (indicators/channels.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.indicators.channels import (
    donchian_lower_series,
    donchian_upper_series,
    get_donchian,
    get_keltner,
    keltner_series,
)


def make_df(close: np.ndarray) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    n = len(close)
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="4h"),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.ones(n),
        }
    )


def test_donchian_excludes_current_bar():
    high = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    up = donchian_upper_series(high, period=2)
    # upper[i] = max(high[i-2:i]) — must NOT include high[i]
    assert np.isnan(up[0]) and np.isnan(up[1])
    assert up[2] == 2.0  # max(high[0:2]) = max(1,2)
    assert up[5] == 5.0  # max(high[3:5]) = max(4,5), not 6


def test_donchian_lower_excludes_current_bar():
    low = np.array([6.0, 5.0, 4.0, 3.0, 2.0, 1.0])
    dn = donchian_lower_series(low, period=2)
    assert dn[2] == 5.0  # min(low[0:2]) = min(6,5)
    assert dn[5] == 2.0  # min(low[3:5]) = min(3,2), not 1


def test_get_donchian_matches_series_last_bar():
    close = 100.0 + np.arange(60, dtype=float)
    df = make_df(close)
    up_s = donchian_upper_series(df["high"].to_numpy(), 20)
    dn_s = donchian_lower_series(df["low"].to_numpy(), 20)
    up, dn = get_donchian(df, period=20)
    assert up == up_s[-1]
    assert dn == dn_s[-1]


def test_get_donchian_insufficient_history():
    df = make_df(np.arange(10, dtype=float) + 100.0)
    up, dn = get_donchian(df, period=20)
    assert np.isnan(up) and np.isnan(dn)


def test_keltner_band_straddles_middle_and_matches_scalar():
    close = 100.0 + 5.0 * np.sin(np.arange(80) / 6.0)
    df = make_df(close)
    up_s, dn_s = keltner_series(
        df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy(), period=20, mult=2.0
    )
    assert np.all(up_s[20:] >= dn_s[20:])  # upper band never below lower
    up, dn = get_keltner(df, period=20, mult=2.0)
    assert up == up_s[-1]
    assert dn == dn_s[-1]


def test_channel_causality_prefix_matches_full():
    close = 100.0 + np.cumsum(np.linspace(-0.3, 0.3, 80))
    high = close * 1.01
    full = donchian_upper_series(high, 20)
    k = 60
    prefix = donchian_upper_series(high[:k], 20)
    assert prefix[-1] == full[k - 1]  # rolling only looks back -> no lookahead
