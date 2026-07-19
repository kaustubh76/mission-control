"""breakout adapter MECHANISM: the Donchian membership state machine — ENTER above the
prior entry_lb-bar high, EXIT below the prior (shorter) exit_lb-bar low (asymmetric, the
fast exit replaces the absent AMM stop) — plus the regime-cap contraction (sum(row) ≤ cap)."""

from __future__ import annotations

import numpy as np

from ictbot.strategy import registry
from ictbot.strategy.adapters.breakout import BreakoutStrategy
from ictbot.strategy.momentum_allocator import AllocatorParams


def _step_then_drop(n: int = 80) -> np.ndarray:
    """Flat baseline → a clean breakout above the prior-20 high → hold → a sharp drop
    below the prior-10 low."""
    x = np.full(n, 100.0)
    x[30] = 110.0  # break out above the 20-bar high
    x[31:50] = 112.0  # hold above
    x[50:] = 90.0  # drop below the 10-bar low (and stay)
    return x


def test_membership_enters_on_breakout_and_exits_on_breakdown():
    bo = BreakoutStrategy(entry_lb=20, exit_lb=10)
    in_set = bo._membership(_step_then_drop().reshape(-1, 1))[:, 0]
    assert bool(in_set[30])  # entered at the breakout bar
    assert bool(in_set[45])  # still in while above the exit low
    assert not bool(in_set[55])  # exited after the breakdown


def test_exit_channel_is_faster_than_entry():
    bo = BreakoutStrategy(entry_lb=20, exit_lb=10)
    assert bo.exit_lb < bo.entry_lb  # asymmetric: the exit reacts sooner than the entry


def test_registered_default_is_robust_config():
    # Re-registered to the stability-sweep ROBUST config: entry 20 / exit 5 / 12h rebalance
    # (entry20/exit10/rb6 was UNSTABLE). The BNB_STRATEGY_05 alias inherits this.
    bo = registry.get("breakout")
    assert bo.entry_lb == 20 and bo.exit_lb == 5
    assert bo.default_params().rebal_bars == 3


def test_weights_respect_regime_cap():
    bo = BreakoutStrategy()
    close = np.column_stack([_step_then_drop() for _ in range(3)])
    cap = np.full(close.shape[0], 0.5)
    w = bo.weight_path(close, p=AllocatorParams(vol_lookback=10, rebal_bars=1), cap_series=cap)
    assert np.all(w.sum(axis=1) <= 0.5 + 1e-9)  # held set never exceeds the deployment cap
    assert w.sum() > 0.0  # but it DID deploy while broken out
