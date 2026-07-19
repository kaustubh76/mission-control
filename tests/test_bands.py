"""Tests for rolling-stat band primitives (indicators/bands.py)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.indicators.bands import (
    bollinger_series,
    get_bollinger,
    get_rolling_std,
    get_rolling_zscore,
    rolling_std_series,
    rolling_zscore_series,
)


def make_df(close: np.ndarray) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    n = len(close)
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="4h"),
            "close": close,
        }
    )


def test_flat_series_zero_std_and_zscore():
    x = np.full(50, 100.0)
    std = rolling_std_series(x, 20)
    z = rolling_zscore_series(x, 20)
    assert std[-1] == 0.0
    assert z[-1] == 0.0  # flat window -> neutral, not nan/inf


def test_rolling_std_matches_numpy_population():
    rng = np.random.default_rng(0)
    x = rng.normal(100, 5, 80)
    s = rolling_std_series(x, 20)
    assert abs(s[-1] - np.std(x[-20:])) < 1e-9  # ddof=0 (population), matches numpy


def test_zscore_sign_and_magnitude():
    x = np.concatenate([np.full(30, 100.0), [130.0]])  # a spike above the mean
    z = rolling_zscore_series(x, 20)
    assert z[-1] > 0  # above-mean -> positive z


def test_bollinger_flat_collapses_to_mid():
    x = np.full(40, 100.0)
    mid, up, dn = bollinger_series(x, 20, 2.0)
    assert up[-1] == mid[-1] == dn[-1] == 100.0


def test_bollinger_ramp_pins_upper_band():
    x = 100.0 + np.arange(40, dtype=float)  # steady ramp -> last close near/above upper band
    mid, up, dn = bollinger_series(x, 20, 2.0)
    assert up[-1] > mid[-1] > dn[-1]
    assert x[-1] > mid[-1]  # a rising price sits above its own moving average


def test_get_scalars_match_series_last_bar():
    rng = np.random.default_rng(1)
    x = rng.normal(100, 4, 60)
    df = make_df(x)
    assert get_rolling_std(df, 20) == rolling_std_series(x, 20)[-1]
    assert get_rolling_zscore(df, 20) == rolling_zscore_series(x, 20)[-1]
    mid, up, dn = get_bollinger(df, 20, 2.0)
    s_mid, s_up, s_dn = bollinger_series(x, 20, 2.0)
    assert (mid, up, dn) == (s_mid[-1], s_up[-1], s_dn[-1])


def test_bands_causality_prefix_matches_full():
    rng = np.random.default_rng(2)
    x = rng.normal(100, 4, 80)
    full = rolling_std_series(x, 20)
    k = 60
    prefix = rolling_std_series(x[:k], 20)
    assert abs(prefix[-1] - full[k - 1]) < 1e-12


def test_get_scalars_insufficient_history():
    df = make_df(np.arange(5, dtype=float) + 100.0)
    assert get_rolling_std(df, 20) == 0.0
    assert get_rolling_zscore(df, 20) == 0.0
    assert np.isnan(get_bollinger(df, 20)[0])
