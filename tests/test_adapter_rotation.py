"""rotation adapter MECHANISM (weight_path): always holds exactly top_k=3 names, has NO
absolute cash filter (stays deployed on a broad mild downtrend where the momentum arm would
cash out), and reduces exactly to the underlying multi-lookback ranked path."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy.adapters.rotation import RotationStrategy
from ictbot.strategy.momentum_allocator import AllocatorParams

P = AllocatorParams(lookback=20, vol_lookback=10, rebal_bars=3)


def _distinct_slopes(n: int = 160, k: int = 6) -> np.ndarray:
    """Distinct linear slopes → a deterministic cross-sectional rank order."""
    return np.column_stack([100.0 + (j + 1) * 0.4 * np.arange(n) for j in range(k)])


def _mild_downtrend(n: int = 160, k: int = 6) -> np.ndarray:
    """Gentle broad decline: abs_filter=True would cash out; rotation stays deployed."""
    return np.column_stack([200.0 - (j + 1) * 0.05 * np.arange(n) for j in range(k)])


def test_holds_exactly_top_k_3():
    rot = RotationStrategy()
    close = _distinct_slopes()
    w = rot.weight_path(close, p=P)
    warm = _ma.warmup(rot._p(P))
    for i in range(warm, close.shape[0]):
        assert int((w[i] > 0).sum()) == 3, (i, w[i])


def test_stays_deployed_without_cash_filter():
    rot = RotationStrategy()
    close = _mild_downtrend()
    w = rot.weight_path(close, p=P)
    # rotation (abs_filter=False) keeps holding the relatively-strongest names...
    assert w[-1].sum() > 0.0
    # ...whereas the SAME params WITH the cash filter would go all-USDT on a broad decline.
    assert _ma.weight_path(close, replace(P, abs_filter=True))[-1].sum() == 0.0


def test_matches_ranked_blend_path():
    rot = RotationStrategy()
    close = _distinct_slopes()
    expected = _ma.weight_path_ranked(close, rot._p(P), None, blend=RotationStrategy.BLEND)
    assert np.array_equal(rot.weight_path(close, p=P), expected)
