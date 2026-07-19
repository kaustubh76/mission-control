"""
Rolling-statistic bands — rolling std, z-score, Bollinger.

Used by the mean-reversion strategy (SIM-research) and the vol-targeting overlay
(basket realized vol). Two shapes per indicator, matching the house convention:
  - `get_*(df) -> latest scalar(s)`   (live, DataFrame in)
  - `*_series(np.ndarray) -> full (n,) series`  (vectorised backtest, numpy in)

std uses ddof=0 (population) to match the numpy `.std()` convention used in
momentum_allocator / regime_score, so z-scores compose consistently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_mean_series(x: np.ndarray, window: int) -> np.ndarray:
    return pd.Series(x).rolling(window).mean().to_numpy()


def rolling_std_series(x: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling standard deviation (population, ddof=0). NaN during warmup."""
    return pd.Series(x).rolling(window).std(ddof=0).to_numpy()


def rolling_zscore_series(x: np.ndarray, window: int) -> np.ndarray:
    """(x - rolling_mean) / rolling_std. 0.0 where std == 0 (flat window)."""
    mean = rolling_mean_series(x, window)
    std = rolling_std_series(x, window)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (np.asarray(x, dtype=float) - mean) / std
    z[~np.isfinite(z)] = 0.0  # std==0 (flat) or warmup-NaN -> neutral 0
    return z


def bollinger_series(
    close: np.ndarray, window: int = 20, n_std: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger (mid, upper, lower): SMA(window) ± n_std * rolling_std(window)."""
    mid = rolling_mean_series(close, window)
    sd = rolling_std_series(close, window)
    return mid, mid + n_std * sd, mid - n_std * sd


def get_rolling_std(df: pd.DataFrame, window: int = 20) -> float:
    """Latest rolling std of df['close']; 0.0 on insufficient history."""
    if len(df) < window:
        return 0.0
    return float(rolling_std_series(df["close"].to_numpy(dtype=float), window)[-1])


def get_rolling_zscore(df: pd.DataFrame, window: int = 20) -> float:
    """Latest rolling z-score of df['close']; 0.0 on insufficient history."""
    if len(df) < window:
        return 0.0
    return float(rolling_zscore_series(df["close"].to_numpy(dtype=float), window)[-1])


def get_bollinger(
    df: pd.DataFrame, window: int = 20, n_std: float = 2.0
) -> tuple[float, float, float]:
    """Latest (mid, upper, lower) Bollinger band; (nan, nan, nan) on insufficient history."""
    if len(df) < window:
        nan = float("nan")
        return (nan, nan, nan)
    mid, up, dn = bollinger_series(df["close"].to_numpy(dtype=float), window, n_std)
    return (float(mid[-1]), float(up[-1]), float(dn[-1]))
