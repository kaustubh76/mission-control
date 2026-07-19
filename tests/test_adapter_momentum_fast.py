"""momentum_fast adapter MECHANISM: it is momentum_adaptive's machinery with ONLY the horizon
levers overridden — lookback 60 (~10d) + 12h rebalance (rebal_bars=3) — so default_params carries
those, weight_path applies them (bit-for-bit the locked path with the fast levers), and the
resulting book differs from the 120-bar/daily incumbent. Offline — synthetic matrices, no network."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy import registry
from ictbot.strategy.adapters.momentum import FastMomentumStrategy
from ictbot.strategy.momentum_allocator import AllocatorParams


def _close(n: int = 200, k: int = 6) -> np.ndarray:
    rng = np.arange(n)
    return np.column_stack(
        [
            100.0 * (1.0 + 0.002 * (j + 1)) ** rng * (1.0 + 0.03 * np.sin(rng / 7.0 + j))
            for j in range(k)
        ]
    )


def test_fast_default_params_override():
    p = FastMomentumStrategy().default_params()
    assert p.lookback == 60 and p.rebal_bars == 3  # the only levers it changes


def test_fast_weight_path_applies_the_fast_levers():
    close = _close()
    out = registry.get("momentum_fast").weight_path(close, p=AllocatorParams())  # base params in…
    exp = _ma.weight_path(
        close, replace(AllocatorParams(), lookback=60, rebal_bars=3)
    )  # …fast levers out
    assert np.array_equal(out, exp)


def test_fast_book_differs_from_incumbent():
    close = _close()
    fast = registry.get("momentum_fast").weight_path(close, p=AllocatorParams())
    base = registry.get("momentum_adaptive").weight_path(close, p=AllocatorParams())
    assert not np.array_equal(fast, base)  # shorter lookback + faster rebal → different book
