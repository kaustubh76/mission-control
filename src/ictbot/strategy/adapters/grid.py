"""
Grid / range strategy — a NET-INVENTORY target-weight book (the last playbook arm).

A classic grid rests buy orders below + sell orders above the price; this venue has no resting
two-sided orders (TWAK signs spot only, the portfolio backtester consumes only a target-weight
matrix). So the grid is re-expressed as a price-responsive TARGET WEIGHT: hold MORE of a token the
lower it sits in its recent range (accumulate the dips), LESS the higher (distribute the rips), with a
HARD RANGE STOP — flatten a token the moment it breaks BELOW the range, so a breakdown can't pin the
book max-long into a falling knife (the negative-skew tail that makes a naive grid the worst risk
profile for a DD-gated contest, docs/strategy_playbook.md #5).

Mechanism per token j, on a Donchian range (the SAME prior-window high/low primitive `breakout` uses):
  upper = donchian_upper(close, window)   # prior `window`-bar high
  lower = donchian_lower(close, window)   # prior `window`-bar low
  pos   = clip((upper - close) / (upper - lower), 0, 1)   # ~1 at the range BOTTOM, 0 at the TOP
  if close < lower:  pos = 0                              # HARD RANGE STOP (breakdown → flatten)
The held set is sized by pos × inverse-vol, scaled by the regime cap (sum(row) ≤ cap → DQ-safe), and
rebalanced on `rebal_bars`. A forward-gated capability arm (uniform promotion policy:
scripts/validate_strategy.py); the playbook ranks grid BELOW-AVG — expect the gates to grade it
accordingly, that is a valid result, not an edge claim.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.indicators.channels import donchian_lower_series, donchian_upper_series
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.regime_score import adaptive_cap, regime_score
from ictbot.strategy.registry import StratContext, WeightDecision


class GridStrategy:
    """Net-inventory grid: buy low-in-range / sell high-in-range, flatten on a breakdown."""

    name = "grid"

    def __init__(self, window: int = 50):
        self.window = window

    def for_daily(self) -> GridStrategy:
        """DAILY-grid coarsening of the range window (6 x 4h = 1 day) for the CEX-free cmc_daily
        survival backtest: a 50-bar range on 4h ≈ ~8-day on daily (conservative). Live uses 4h."""
        return GridStrategy(window=max(3, round(self.window / 6)))

    def _inventory(self, close: np.ndarray) -> np.ndarray:
        """(n, k) grid inventory in [0,1]: ~1 at the range bottom, 0 at the top, 0 on a breakdown."""
        n, k = close.shape
        upper = np.column_stack([donchian_upper_series(close[:, j], self.window) for j in range(k)])
        lower = np.column_stack([donchian_lower_series(close[:, j], self.window) for j in range(k)])
        span = upper - lower
        with np.errstate(divide="ignore", invalid="ignore"):
            pos = (upper - close) / span
        pos = np.where(span > 0, pos, 0.0)
        pos = np.clip(pos, 0.0, 1.0)
        pos = np.where(close < lower, 0.0, pos)  # hard range stop (breakdown)
        pos = np.where(np.isnan(upper) | np.isnan(lower), 0.0, pos)  # warmup
        return pos

    def _size(
        self,
        rets: np.ndarray,
        i: int,
        pos_row: np.ndarray,
        p: AllocatorParams,
        eff_cap: float,
        k: int,
    ) -> np.ndarray:
        row = np.zeros(k)
        members = np.where(pos_row > 0)[0]
        if len(members) == 0:
            return row
        if p.inverse_vol and i >= p.vol_lookback:
            vol = np.array([rets[i - p.vol_lookback + 1 : i + 1, j].std() or 1e-9 for j in members])
            raw = pos_row[members] / vol  # grid depth × inverse-vol
        else:
            raw = pos_row[members]
        tot = raw.sum()
        if tot <= 0:
            return row
        row[members] = (raw / tot) * eff_cap
        return row

    def weight_path(self, close, *, p, cap_series=None):
        n, k = close.shape
        rets = np.vstack([np.zeros(k), close[1:] / close[:-1] - 1.0])
        pos = self._inventory(close)
        w = np.zeros((n, k))
        cur = np.zeros(k)
        for i in range(n):
            if i % p.rebal_bars == 0:
                eff_cap = p.deploy_cap if cap_series is None else float(cap_series[i])
                cur = self._size(rets, i, pos[i], p, eff_cap, k)
            w[i] = cur
        return w

    def target_weights_now(self, close_df: pd.DataFrame, *, ctx: StratContext) -> WeightDecision:
        cols = [c for c in close_df.columns if c in CONTEST_TOKENS]
        sub = close_df[cols].dropna()
        p = ctx.params
        if len(sub) < self.warmup(p):
            return WeightDecision({c: 0.0 for c in cols}, 0.0, ctx.floor)
        full = sub.to_numpy(dtype=float)
        score = regime_score(
            full,
            len(sub) - 1,
            ma_window=ctx.ma_window,
            fear_greed=ctx.fear_greed,
            intel=ctx.intel,
            ta_health=ctx.ta_health,
            w_ta=ctx.w_ta,
        )
        cap = adaptive_cap(score, ctx.floor, ctx.ceiling)
        rank_cols = cols if not ctx.active else [c for c in cols if c in set(ctx.active)] or cols
        sub_r = sub[rank_cols].to_numpy(dtype=float)
        rets_r = np.vstack([np.zeros(sub_r.shape[1]), sub_r[1:] / sub_r[:-1] - 1.0])
        i = len(sub_r) - 1
        row = self._size(rets_r, i, self._inventory(sub_r)[i], p, cap, sub_r.shape[1])
        weights = {c: 0.0 for c in cols}
        weights.update({c: float(row[j]) for j, c in enumerate(rank_cols)})
        return WeightDecision(weights, score, cap)

    def default_params(self):
        return AllocatorParams()

    def warmup(self, p):
        return max(self.window, p.vol_lookback) + 1

    def summary(self, p, *, n_tokens):
        return (
            f"Grid / range: hold MORE of each of the {n_tokens} tokens the lower it sits in its "
            f"{self.window}-bar range (buy dips), less the higher; flatten on a breakdown below the "
            f"range (hard stop). Inverse-vol, regime-capped. Net-inventory model — no resting orders."
        )
