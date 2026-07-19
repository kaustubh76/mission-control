"""grid adapter MECHANISM: net-inventory in a Donchian range (more inventory the lower in range,
mean-reverting/monotonic), the HARD RANGE STOP (flatten on a breakdown below the prior range), and
the regime-cap contraction. Deterministic synthetic matrices, no network."""

from __future__ import annotations

import numpy as np

from ictbot.strategy.adapters.grid import GridStrategy
from ictbot.strategy.momentum_allocator import AllocatorParams

WIN = 20


def _ranged_col(test_val: float, n: int = 60) -> np.ndarray:
    """bars 0..WIN-1 descend 110→90 (establishes the prior range [90,110]); bars WIN.. = test_val,
    so the inventory at bar WIN reflects `test_val` against that range."""
    x = np.empty(n)
    x[:WIN] = np.linspace(110.0, 90.0, WIN)
    x[WIN:] = test_val
    return x


def _pos(test_val: float) -> float:
    return float(GridStrategy(window=WIN)._inventory(_ranged_col(test_val).reshape(-1, 1))[WIN, 0])


def test_inventory_increases_toward_range_bottom():
    assert _pos(90.0) == 1.0  # at the range bottom → max inventory (buy the dip)
    assert _pos(110.0) == 0.0  # at the top → flat
    assert abs(_pos(100.0) - 0.5) < 1e-9  # mid-range → half
    assert _pos(95.0) > _pos(105.0)  # mean-reverting: lower in range → more inventory


def test_breakdown_below_range_flattens():
    # close below the prior-window low is a breakdown → hard stop flattens (NOT max-long on a knife)
    assert _pos(85.0) == 0.0


def test_weights_respect_cap_and_warmup():
    g = GridStrategy(window=WIN)
    close = np.column_stack([_ranged_col(92.0) for _ in range(3)])  # all 3 sit mid-low in range
    cap = np.full(close.shape[0], 0.5)
    w = g.weight_path(close, p=AllocatorParams(vol_lookback=10, rebal_bars=1), cap_series=cap)
    assert np.all(w.sum(axis=1) <= 0.5 + 1e-9)  # held set never exceeds the deployment cap
    assert (w[:WIN] == 0).all()  # Donchian warmup (prior range NaN) → no position
    assert w[30].sum() > 0  # deployed once the range is established + in-range
