"""
Dual-momentum cash-out strategy (the reference NEW strategy).

Classic dual momentum (Antonacci) = RELATIVE momentum (own the strongest names) +
ABSOLUTE momentum (only be in-market when momentum beats cash, else rotate to USDT).
On the locked allocator this is a thin specialization:

  - RELATIVE + per-token ABSOLUTE: force ``abs_filter=True`` so the existing ranker
    holds top-k only among tokens with positive trailing return (else USDT). This
    reuses ``momentum_allocator`` / ``regime_score`` verbatim.
  - BASKET-level ABSOLUTE kill: additionally go FULLY to USDT when the equal-weight
    basket index is itself below its level ``abs_lookback`` bars ago — the "absolute
    momentum vs cash" leg applied to the whole book, the native long-only risk-off.

No execution change is needed: an all-zero weight book → ``target={}`` →
``TwakSpotBroker.rebalance({})`` sells everything to USDT via the existing
sell-first loop. A forward-gated capability arm (uniform promotion policy:
scripts/validate_strategy.py) — regime-diversification, not an edge claim.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy import regime_score as _rs
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.registry import StratContext, WeightDecision


class DualMomentumStrategy:
    """Relative + absolute momentum with a basket-level cash-out to USDT."""

    name = "dual_momentum"

    def __init__(self, abs_lookback: int | None = None):
        # None -> use the params' momentum lookback as the basket absolute horizon.
        self.abs_lookback = abs_lookback

    def _abs_lb(self, p: AllocatorParams) -> int:
        return self.abs_lookback or p.lookback

    def _p2(self, p: AllocatorParams) -> AllocatorParams:
        return replace(p, abs_filter=True)  # force the per-token cash filter ON

    def weight_path(self, close, *, p, cap_series=None):
        p2 = self._p2(p)
        w = _ma.weight_path(close, p2, cap_series)
        idx = _rs.index_series(close)
        lb = self._abs_lb(p2)
        # Basket absolute-momentum kill: whole row -> all USDT when the basket is down.
        for i in range(close.shape[0]):
            if i >= lb and idx[i] <= idx[i - lb]:
                w[i] = 0.0
        return w

    def target_weights_now(self, close_df: pd.DataFrame, *, ctx: StratContext) -> WeightDecision:
        p2 = self._p2(ctx.params)
        active = list(ctx.active) if ctx.active else None
        w, score, cap = _rs.adaptive_target_weights(
            close_df,
            p2,
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
        cols = [c for c in close_df.columns if c in CONTEST_TOKENS]
        sub = close_df[cols].dropna()
        lb = self._abs_lb(p2)
        if len(sub) > lb:
            idx = _rs.index_series(sub.to_numpy(dtype=float))
            if idx[-1] <= idx[-1 - lb]:  # basket down over the horizon -> all cash
                w = {k: 0.0 for k in w}
                cap = 0.0
        return WeightDecision(w, score, cap)

    def default_params(self):
        return AllocatorParams(abs_filter=True)

    def warmup(self, p):
        return max(_ma.warmup(p), self._abs_lb(p) + 1)

    def summary(self, p, *, n_tokens):
        return (
            f"Dual momentum: hold top-{p.top_k} of {n_tokens} by {p.lookback}-bar "
            f"return with a positive-momentum cash filter; rotate FULLY to USDT when "
            f"the basket itself is down over {self._abs_lb(p)} bars. Long-only risk-off."
        )
