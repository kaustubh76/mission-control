"""mean_reversion adapter MECHANISM: the oversold trigger fires only on a column trading
> threshold z-scores below its rolling mean; flat columns have z==0 (the std==0 neutral
guard) and never fire. Plus inverse-vol sizing under the regime-cap contraction."""

from __future__ import annotations

import numpy as np

from ictbot.indicators.bands import rolling_zscore_series
from ictbot.strategy.adapters.mean_reversion import MeanReversionStrategy
from ictbot.strategy.momentum_allocator import AllocatorParams


def _one_dipper(n: int = 80, k: int = 3, dip_at: int = 50) -> np.ndarray:
    """cols 0..k-2 flat (z==0, never oversold); the last col drops sharply (z ≪ -1)."""
    close = np.full((n, k), 100.0)
    close[dip_at:, k - 1] = 80.0
    return close


def test_only_dipping_column_is_oversold():
    mr = MeanReversionStrategy(window=20, threshold=1.0)
    close = _one_dipper()
    oversold = mr._oversold(close)
    assert bool(oversold[50, 2])  # the dip column is oversold at the drop
    assert not oversold[:, 0].any()  # flat columns never fire...
    assert not oversold[:, 1].any()
    assert rolling_zscore_series(close[:, 2], 20)[50] < -1.0  # cross-check the raw z-score


def test_only_oversold_column_is_held_and_capped():
    mr = MeanReversionStrategy(window=20, threshold=1.0)
    close = _one_dipper()
    cap = np.full(close.shape[0], 0.5)
    w = mr.weight_path(close, p=AllocatorParams(vol_lookback=10, rebal_bars=1), cap_series=cap)
    assert w[50, 2] > 0.0  # the dipper is bought
    assert w[50, 0] == 0.0 and w[50, 1] == 0.0  # the flats are not
    assert np.all(w.sum(axis=1) <= 0.5 + 1e-9)  # never exceeds the deployment cap
