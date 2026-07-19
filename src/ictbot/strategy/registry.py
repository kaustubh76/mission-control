"""
Pluggable portfolio-strategy registry.

The contest ships ONE locked strategy (the regime-adaptive long-only spot momentum
allocator). This module is the seam that lets the product hold MORE strategies
without touching that locked path: every allocation strategy is registered by name
and selected via ``settings.strategy_name``, and the runtime dispatches through
``registry.get(name)`` instead of a hardcoded if/elif.

A "portfolio strategy" emits TARGET WEIGHTS over the token universe (the remainder is
USDT) — the shape the strategy-agnostic execution layer
(``exec/bsc_spot_live.TwakSpotBroker.rebalance``) and backtester
(``engine/portfolio_replay.evaluate``) already consume. New strategies therefore need
ZERO execution / backtest / dashboard changes; they implement this contract and
register.

The locked momentum allocator is itself registered (``adapters/momentum.py``, thin
wrappers that delegate to the existing, tested functions) so the default path is
bit-for-bit unchanged — see ``tests/test_strategy_registry.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from ictbot.strategy.momentum_allocator import AllocatorParams


@dataclass(frozen=True)
class StratContext:
    """Per-tick live context handed to a strategy's ``target_weights_now``.

    Carries the OPTIONAL enrichment the regime-adaptive momentum path needs
    (Fear&Greed, CMC intel/TA, the UI active-token set, the deployment band). A
    strategy ignores the fields it doesn't use, so the generic contract stays small
    while the locked path still receives everything it expects today. ``intel`` is
    typed loosely (``regime_score.RegimeIntel | None``) to avoid an import cycle.
    """

    params: AllocatorParams
    active: tuple[str, ...] | list[str] | None = None
    deploy_cap: float = 0.60
    floor: float = 0.40
    ceiling: float = 0.85
    ma_window: int = 50
    fear_greed: int | None = None
    intel: object | None = None
    ta_health: float | None = None
    w_ta: float = 1.0
    ta_token_scores: dict[str, float] | None = None
    w_ta_rank: float = 0.0


@dataclass(frozen=True)
class WeightDecision:
    """A strategy's live decision: target weights + the regime score & deployment cap
    it acted on. ``score``/``cap`` may be None for strategies with no regime layer
    (journaled as-is)."""

    weights: dict[str, float]
    score: float | None = None
    cap: float | None = None


@runtime_checkable
class PortfolioStrategy(Protocol):
    """Contract every allocation strategy implements (structural — no subclassing)."""

    name: str

    def target_weights_now(self, close_df: pd.DataFrame, *, ctx: StratContext) -> WeightDecision:
        """Live path: target weights from the last row of an aligned close frame."""
        ...

    def weight_path(
        self, close: np.ndarray, *, p, cap_series: np.ndarray | None = None
    ) -> np.ndarray:
        """Vectorised backtest path: an (n, k) target-weight matrix."""
        ...

    def default_params(self):
        """The strategy's params dataclass with defaults."""
        ...

    def warmup(self, p) -> int:
        """Bars of history required before the strategy can act."""
        ...

    def summary(self, p, *, n_tokens: int) -> str:
        """One-line natural-language description for the dashboard."""
        ...


_REGISTRY: dict[str, PortfolioStrategy] = {}
_BUILTINS_DONE = False

# ── Contest naming queue (EDIT THIS as forward testing picks a winner) ──────────
# Branded, stable contest-facing names mapped onto the descriptive arms. Each is a
# selectable alias (STRATEGY_NAME=BNB_STRATEGY_02 + the dashboard SIM selector) that is
# bit-for-bit its target arm. This is the "learning graph": reassign a number to a
# different arm here as you forward-validate, without touching any strategy logic.
# 01 is the incumbent (the locked contest default); 02+ are challengers under test.
# Reassigning here does NOT change the LIVE default — that stays momentum_adaptive
# until an operator sets STRATEGY_NAME in .env (alias-only by policy).
CONTEST_ALIASES: dict[str, str] = {
    "BNB_STRATEGY_01": "momentum_adaptive",  # incumbent — the current contest default
    "BNB_STRATEGY_02": "momentum_voltarget",  # challenger: best risk-adjusted (DD ~14%)
    "BNB_STRATEGY_03": "dual_momentum",  # challenger: lowest DD (~13%), cash-out risk-off
    "BNB_STRATEGY_04": "rotation",  # challenger: top-3 relative-strength rotation
    "BNB_STRATEGY_05": "breakout",  # challenger: Donchian breakout book
    "BNB_STRATEGY_06": "momentum_fast",  # challenger: short-horizon (12h rebal)
    "BNB_STRATEGY_07": "momentum_mafilter",  # challenger: MA trend filter
    "BNB_STRATEGY_08": "mean_reversion",  # research: adverse prior (do not deploy blind)
    "BNB_STRATEGY_09": "grid",  # research: net-inventory grid + hard range stop
}


def alias_target(name: str) -> str | None:
    """If `name` is a registered AliasStrategy, the underlying arm it points at, else None."""
    s = _REGISTRY.get(name)
    return getattr(s, "alias_of", None)


def register(strat: PortfolioStrategy) -> None:
    _REGISTRY[strat.name] = strat


def get(name: str) -> PortfolioStrategy:
    register_builtins()
    if name not in _REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def available() -> list[str]:
    register_builtins()
    return sorted(_REGISTRY)


def register_builtins() -> None:
    """Idempotently register the in-tree strategies. Imported lazily (the adapters
    import this module for the dataclasses) so there is no import cycle."""
    global _BUILTINS_DONE
    if _BUILTINS_DONE:
        return
    from ictbot.strategy.adapters import breakout as _bo
    from ictbot.strategy.adapters import dual_momentum as _dm
    from ictbot.strategy.adapters import grid as _grid
    from ictbot.strategy.adapters import mean_reversion as _mr
    from ictbot.strategy.adapters import momentum as _m
    from ictbot.strategy.adapters import momentum_cmc as _mc
    from ictbot.strategy.adapters import rotation as _rot
    from ictbot.strategy.overlays.base import OverlayStrategy
    from ictbot.strategy.overlays.ma_filter import MaFilterOverlay
    from ictbot.strategy.overlays.vol_target import VolTargetOverlay

    # Locked contest path (default) — bit-for-bit unchanged.
    register(_m.MomentumAllocatorStrategy())
    register(_m.AdaptiveMomentumStrategy())
    # Long-only-spot capability arms (SIM track, gate-promoted before any LIVE use).
    register(_m.FastMomentumStrategy())
    register(_dm.DualMomentumStrategy())
    register(_rot.RotationStrategy())
    register(_bo.BreakoutStrategy())
    register(_mr.MeanReversionStrategy())  # forward-gated arm; adverse prior (see its docstring)
    register(_grid.GridStrategy())  # net-inventory grid + hard range stop (playbook: below-avg)
    register(_mc.CMCMomentumStrategy())  # CMC-driven: decides on CMC's own 4h candles (cmc_stream)
    # Composable overlay variants of the regime-adaptive base (de-risk only).
    register(
        OverlayStrategy(
            _m.AdaptiveMomentumStrategy(), [VolTargetOverlay()], name="momentum_voltarget"
        )
    )
    register(
        OverlayStrategy(
            _m.AdaptiveMomentumStrategy(), [MaFilterOverlay()], name="momentum_mafilter"
        )
    )
    # Branded contest naming queue (BNB_STRATEGY_0X) — thin aliases over the arms above.
    from ictbot.strategy.adapters.alias import AliasStrategy

    for alias, target in CONTEST_ALIASES.items():
        if target in _REGISTRY:
            register(AliasStrategy(alias, _REGISTRY[target]))
    _BUILTINS_DONE = True
