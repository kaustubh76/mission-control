"""
Cross-sectional relative-strength rotation.

Hold the top-K strongest names by a MULTI-LOOKBACK blended momentum z-score, rotating
each rebalance. Distinct from `momentum`/`dual_momentum` in two ways: (1) NO absolute
cash filter (`abs_filter=False` — it's always deployed up to the regime cap, pure
relative strength), and (2) a blend of lookbacks ({120: 0.6, 60: 0.4}) instead of a
single horizon. Both pieces ALREADY exist in momentum_allocator — `weight_path_ranked`
(backtest) and `_weights_at_ranked` (live) — so this adapter is pure reuse, no new
ranker. A forward-gated capability arm (uniform promotion policy:
scripts/validate_strategy.py) — regime-diversification, not an edge claim. The playbook
ranks CS rotation as the *weaker* momentum form, and the regime cap is its only DD
defense since it never cashes out on its own.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from ictbot.strategy import momentum_allocator as _ma
from ictbot.strategy import regime_score as _rs
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.registry import StratContext, WeightDecision


class RotationStrategy:
    """Top-K relative-strength rotation with a multi-lookback blend (always deployed)."""

    name = "rotation"
    BLEND = {120: 0.6, 60: 0.4}

    def _p(self, p: AllocatorParams) -> AllocatorParams:
        return replace(p, top_k=3, abs_filter=False)  # rotation = always pick top-k, no cash filter

    def weight_path(self, close, *, p, cap_series=None):
        return _ma.weight_path_ranked(close, self._p(p), cap_series, blend=self.BLEND)

    def target_weights_now(self, close_df: pd.DataFrame, *, ctx: StratContext) -> WeightDecision:
        p = self._p(ctx.params)
        cols = [c for c in close_df.columns if c in CONTEST_TOKENS]
        sub = close_df[cols].dropna()
        if len(sub) < max(_ma.warmup(p), ctx.ma_window + 1):
            return WeightDecision({c: 0.0 for c in cols}, 0.0, ctx.floor)
        close = sub.to_numpy(dtype=float)
        i = len(sub) - 1
        # Regime score/cap on the FULL universe (market gauge), ranking on the active subset.
        score = _rs.regime_score(
            close,
            i,
            ma_window=ctx.ma_window,
            fear_greed=ctx.fear_greed,
            intel=ctx.intel,
            ta_health=ctx.ta_health,
            w_ta=ctx.w_ta,
        )
        cap = _rs.adaptive_cap(score, ctx.floor, ctx.ceiling)
        rank_cols = cols if not ctx.active else [c for c in cols if c in set(ctx.active)] or cols
        sub_r = sub[rank_cols]
        close_r = sub_r.to_numpy(dtype=float)
        rets_r = np.vstack([np.zeros(close_r.shape[1]), close_r[1:] / close_r[:-1] - 1.0])
        w = _ma._weights_at_ranked(close_r, rets_r, len(sub_r) - 1, p, cap=cap, blend=self.BLEND)
        weights = {c: 0.0 for c in cols}
        weights.update({c: float(w[j]) for j, c in enumerate(rank_cols)})
        return WeightDecision(weights, score, cap)

    def default_params(self):
        return self._p(AllocatorParams())

    def warmup(self, p):
        return _ma.warmup(self._p(p))

    def summary(self, p, *, n_tokens):
        lbs = "+".join(f"{k}" for k in self.BLEND)
        return (
            f"Relative-strength rotation: hold top-{self._p(p).top_k} of {n_tokens} by a "
            f"blended {lbs}-bar momentum z-score, inverse-vol, regime-capped (no cash filter)."
        )
