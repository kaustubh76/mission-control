"""momentum adapter MECHANISM (the ALLOC_ADAPTIVE=false static-cap path): top-k selection by
trailing return, the cash filter (nothing trends up → all USDT), inverse-vol sizing (calmer name
gets the larger weight), the static deploy-cap ceiling, and bit-for-bit equality to the locked
`momentum_allocator.weight_path`. Offline — deterministic synthetic matrices, no network."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy.adapters.momentum import MomentumAllocatorStrategy
from ictbot.strategy.momentum_allocator import AllocatorParams

# rebal every bar so the last row is freshly computed; small lookbacks keep it fast.
P = AllocatorParams(
    lookback=20, vol_lookback=10, top_k=2, deploy_cap=0.6, rebal_bars=1, abs_filter=True
)


def _rising(n: int = 60, k: int = 4) -> np.ndarray:
    """Exponential risers with DISTINCT rates → trailing return strictly increasing in column."""
    rng = np.arange(n)
    return np.column_stack([100.0 * (1.0 + 0.002 * (j + 1)) ** rng for j in range(k)])


def test_holds_only_top_k_strongest():
    w = MomentumAllocatorStrategy().weight_path(_rising(), p=P)[-1]
    assert set(np.flatnonzero(w)) == {2, 3}  # top_k=2 fastest risers
    assert w[0] == 0.0 and w[1] == 0.0


def test_cash_filter_all_falling_goes_to_usdt():
    rng = np.arange(60)
    falling = np.column_stack([100.0 * (1.0 - 0.003 * (j + 1)) ** rng for j in range(4)])
    w = MomentumAllocatorStrategy().weight_path(falling, p=P)
    assert np.allclose(w, 0.0)  # nothing trends up → 100% USDT


def test_inverse_vol_calmer_name_gets_more_weight():
    rng = np.arange(60)
    base = 100.0 * 1.003**rng
    calm = base * (1.0 + 0.005 * np.sin(rng))  # both rise (held); calm has lower vol
    jagged = base * (1.0 + 0.05 * np.sin(rng))  # 10× the oscillation → higher vol
    w = MomentumAllocatorStrategy().weight_path(
        np.column_stack([calm, jagged]), p=replace(P, top_k=2)
    )[-1]
    assert w[0] > w[1] > 0.0  # inverse-vol tilts to the calmer name


def test_static_deploy_cap_bounds_the_book():
    w = MomentumAllocatorStrategy().weight_path(_rising(), p=P)
    assert np.all(w.sum(axis=1) <= P.deploy_cap + 1e-9)


def test_adapter_is_bitwise_the_locked_path():
    close = _rising()
    p = AllocatorParams(lookback=20, vol_lookback=10, rebal_bars=3)
    assert np.array_equal(
        MomentumAllocatorStrategy().weight_path(close, p=p), _ma.weight_path(close, p)
    )
