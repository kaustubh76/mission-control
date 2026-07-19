"""dual_momentum adapter MECHANISM (weight_path): the basket absolute-momentum kill — the
whole book rotates to USDT when the equal-weight basket index is down over abs_lookback,
even if individual names pass the per-token cash filter; and bit-for-bit equality to the
base abs_filter path when the basket is up (the kill never fires).

These assert the strategy's distinguishing logic, not just registry equivalence."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy.adapters.dual_momentum import DualMomentumStrategy
from ictbot.strategy.momentum_allocator import AllocatorParams

# Small but valid params keep the synthetic matrices fast + deterministic.
P = AllocatorParams(lookback=20, vol_lookback=10, rebal_bars=3, top_k=2, abs_filter=True)


def _falling_basket_one_riser(n: int = 120, k: int = 4) -> np.ndarray:
    """3 cols crash, 1 col rises slightly → basket index DOWN, but the riser passes the
    per-token cash filter, so ONLY the basket kill can zero the whole book."""
    cols = []
    for j in range(k):
        if j == k - 1:
            cols.append(100.0 + 0.05 * np.arange(n))  # slow riser (passes abs_filter)
        else:
            cols.append(200.0 - 1.0 * np.arange(n))  # steep fallers (drag the basket down)
    return np.column_stack(cols)


def _rising_basket(n: int = 120, k: int = 4) -> np.ndarray:
    return np.column_stack([100.0 + (j + 1) * 0.3 * np.arange(n) for j in range(k)])


def test_basket_kill_zeros_book_when_index_down():
    close = _falling_basket_one_riser()
    killed = DualMomentumStrategy().weight_path(close, p=P)
    base = _ma.weight_path(close, replace(P, abs_filter=True))
    # the basket kill drives the WHOLE book to USDT...
    assert np.allclose(killed, 0.0)
    # ...and it is the KILL (not the cash filter) doing it: the base path DID hold the riser.
    assert base.sum() > 0.0


def test_no_kill_when_basket_up_equals_base():
    close = _rising_basket()
    out = DualMomentumStrategy().weight_path(close, p=P)
    base = _ma.weight_path(close, replace(P, abs_filter=True))
    assert np.array_equal(out, base)  # basket up → kill never fires → identical to base
    assert base.sum() > 0.0  # sanity: the base actually deployed
