"""
Registry adapters for the LOCKED momentum allocator.

These wrap the existing, tested functions
(``momentum_allocator.target_weights_now`` / ``weight_path`` and
``regime_score.adaptive_target_weights``) as ``PortfolioStrategy`` implementations.
They add NO logic — every number is produced by the same code the contest has been
validated on, so the default path stays bit-for-bit unchanged. The equivalence is
pinned by ``tests/test_strategy_registry.py``.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy import regime_score as _rs
from ictbot.strategy.momentum_allocator import AllocatorParams
from ictbot.strategy.registry import StratContext, WeightDecision


class MomentumAllocatorStrategy:
    """Static-cap cross-sectional momentum — the ``ALLOC_ADAPTIVE=false`` path."""

    name = "momentum"

    def target_weights_now(self, close_df: pd.DataFrame, *, ctx: StratContext) -> WeightDecision:
        # Mirrors run_allocator's static branch: rank only over the active set
        # (degrade to all columns if active is empty/None), cap = the static dial.
        cols = list(close_df.columns)
        active = set(ctx.active) if ctx.active else None
        rank_cols = [c for c in cols if active is None or c in active] or cols
        w = _ma.target_weights_now(close_df[rank_cols], ctx.params)
        return WeightDecision(w, None, ctx.deploy_cap)

    def weight_path(self, close, *, p, cap_series=None):
        return _ma.weight_path(close, p, cap_series)

    def default_params(self):
        return AllocatorParams()

    def warmup(self, p):
        return _ma.warmup(p)

    def summary(self, p, *, n_tokens):
        return (
            f"Static momentum: hold top-{p.top_k} of {n_tokens} by {p.lookback}-bar "
            f"return, inverse-vol, capped at {p.deploy_cap:.0%}."
        )


class AdaptiveMomentumStrategy:
    """Regime-adaptive cross-sectional momentum — the SHIPPED contest default."""

    name = "momentum_adaptive"

    def target_weights_now(self, close_df: pd.DataFrame, *, ctx: StratContext) -> WeightDecision:
        active = list(ctx.active) if ctx.active else None
        w, score, cap = _rs.adaptive_target_weights(
            close_df,
            ctx.params,
            floor=ctx.floor,
            ceiling=ctx.ceiling,
            ma_window=ctx.ma_window,
            fear_greed=ctx.fear_greed,
            intel=ctx.intel,
            ta_health=ctx.ta_health,
            w_ta=ctx.w_ta,
            ta_token_scores=ctx.ta_token_scores,
            w_ta_rank=ctx.w_ta_rank,
            active=active,
        )
        return WeightDecision(w, score, cap)

    def weight_path(self, close, *, p, cap_series=None):
        # The regime cap enters the backtest via `cap_series` (computed by the
        # validator from regime_score.cap_series), exactly like the locked path.
        return _ma.weight_path(close, p, cap_series)

    def default_params(self):
        return AllocatorParams()

    def warmup(self, p):
        return _ma.warmup(p)

    def summary(self, p, *, n_tokens):
        return (
            f"Regime-adaptive momentum: hold top-{p.top_k} of {n_tokens} by "
            f"{p.lookback}-bar return, inverse-vol; deployment scales with a live "
            f"risk-on score (breadth + trend + vol + Fear&Greed)."
        )


class FastMomentumStrategy(AdaptiveMomentumStrategy):
    """Short-HORIZON regime-adaptive momentum (the "short-spot" arm).

    Identical machinery to ``momentum_adaptive`` but with a SHORT lookback and a
    FASTER rebalance — the campaign-mode levers (docs/bnb_strategy_decision.md §8)
    promoted to a named, registry-selectable strategy. A forward-gated capability arm
    (uniform promotion policy: scripts/validate_strategy.py) — not an edge claim;
    faster horizons historically shed edge. It overrides only ``lookback``/``rebal_bars``;
    everything else (ranking, inverse-vol, regime cap) is the locked code path.
    """

    name = "momentum_fast"
    LOOKBACK = 60  # ~10 days of 4h bars (vs 120 = ~20d)
    REBAL = 3  # 12h rebalance (vs 6 = daily)

    def _fast(self, p: AllocatorParams) -> AllocatorParams:
        return replace(p, lookback=self.LOOKBACK, rebal_bars=self.REBAL)

    def target_weights_now(self, close_df: pd.DataFrame, *, ctx: StratContext) -> WeightDecision:
        return super().target_weights_now(close_df, ctx=replace(ctx, params=self._fast(ctx.params)))

    def weight_path(self, close, *, p, cap_series=None):
        return super().weight_path(close, p=self._fast(p), cap_series=cap_series)

    def default_params(self):
        return self._fast(AllocatorParams())

    def summary(self, p, *, n_tokens):
        return (
            f"Short-horizon momentum: hold top-{p.top_k} of {n_tokens} by "
            f"{self.LOOKBACK}-bar (~10d) return, rebalanced every {self.REBAL} bars "
            f"(12h); regime-adaptive deployment. Faster/reactive variant."
        )
