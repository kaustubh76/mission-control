"""momentum_adaptive adapter MECHANISM (the LOCKED contest incumbent): its backtest path is
bit-for-bit the locked `momentum_allocator.weight_path` (the regime cap enters only via cap_series),
the regime cap actually scales deployment (higher risk-on cap → more deployed), and the live
decision stays inside the [floor, ceiling] band with the book ≤ cap. Pins the incumbent's behavior
so future work can't silently perturb the default. Offline — synthetic matrices, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy import regime_score as rs
from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.registry import StratContext


def _close(n: int = 320, k: int = 8) -> np.ndarray:
    rng = np.arange(n)
    return np.column_stack(
        [
            100.0 * (1.0 + 0.0015 * (j + 1)) ** rng * (1.0 + 0.03 * np.sin(rng / 9.0 + j))
            for j in range(k)
        ]
    )


def test_incumbent_weight_path_is_bitwise_the_locked_path():
    close, p = _close(), AllocatorParams()
    caps = rs.cap_series(close, floor=0.40, ceiling=0.85, ma_window=50)
    arm = registry.get("momentum_adaptive")
    for cs in (None, caps):
        assert np.array_equal(
            arm.weight_path(close, p=p, cap_series=cs), _ma.weight_path(close, p, cs)
        )


def test_regime_cap_scales_deployment():
    close, p = _close(), AllocatorParams()
    arm = registry.get("momentum_adaptive")
    lo = arm.weight_path(close, p=p, cap_series=np.full(len(close), 0.30))
    hi = arm.weight_path(close, p=p, cap_series=np.full(len(close), 0.85))
    assert hi.sum() > lo.sum()  # a risk-on cap deploys strictly more
    assert np.all(lo.sum(axis=1) <= 0.30 + 1e-9)  # and the cap is a hard ceiling


def test_incumbent_live_decision_within_band():
    df = pd.DataFrame(
        _close(),
        columns=list(CONTEST_TOKENS),
        index=pd.date_range("2024-01-01", periods=320, freq="4h"),
    )
    ctx = StratContext(
        params=AllocatorParams(), floor=0.40, ceiling=0.85, ma_window=50, fear_greed=60
    )
    d = registry.get("momentum_adaptive").target_weights_now(df, ctx=ctx)
    assert 0.0 <= d.score <= 1.0
    assert 0.40 - 1e-9 <= d.cap <= 0.85 + 1e-9
    assert sum(d.weights.values()) <= d.cap + 1e-9
