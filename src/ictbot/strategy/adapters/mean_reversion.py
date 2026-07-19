"""
Short-horizon mean-reversion — a forward-gated capability arm with an ADVERSE PRIOR.

Buy oversold names (close > 1 z-score below its rolling mean, i.e. below the lower
Bollinger band), inverse-vol weighted, regime-capped.

Promotion follows the UNIFORM policy (scripts/validate_strategy.py): like every
non-default arm it must clear the survival gate in backtest, then a forward check on
unseen SIM data, then operator sign-off — and the locked momentum_adaptive stays the
default. This arm is NOT singled out as "never deploy"; it is eligible under the same
rule as the others.

What IS different is the PRIOR, not the gate. Gate-A is a SURVIVAL test (DQ-safe + ≥7
trades/wk), not an edge test — there is no long-only TA edge on this universe to fail on
(docs/bnb_strategy_decision.md §1), so this arm clears it like the rest (inverse-vol +
the regime cap keep worst-week DD low). But its mechanism is adverse here: short-term
reversal *flips to momentum* on large liquid majors (Fičura 2023) and the thin edge dies
to ~0.70% AMM friction. So a forward PASS for mean-reversion is the most likely of any
arm to be sample-luck rather than a repeatable edge — treat its promotion with extra
skepticism. Nothing routes it into the default; only a human STRATEGY_NAME can select it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.indicators.bands import rolling_zscore_series
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.regime_score import adaptive_cap, regime_score
from ictbot.strategy.registry import StratContext, WeightDecision


class MeanReversionStrategy:
    """Buy oversold (z < -threshold) names; inverse-vol, regime-capped. Adverse prior."""

    name = "mean_reversion"

    def __init__(self, window: int = 20, threshold: float = 1.0):
        self.window = window
        self.threshold = threshold

    def for_daily(self) -> MeanReversionStrategy:
        """DAILY-grid coarsening of the z-score window (6 x 4h = 1 day) for the CEX-free cmc_daily
        survival backtest: a 20-bar z-score on 4h ≈ ~3-day on daily (conservative). Live uses 4h."""
        return MeanReversionStrategy(
            window=max(3, round(self.window / 6)), threshold=self.threshold
        )

    def _oversold(self, close: np.ndarray) -> np.ndarray:
        z = np.column_stack(
            [rolling_zscore_series(close[:, j], self.window) for j in range(close.shape[1])]
        )
        return z < -self.threshold  # NaN -> False during warmup

    def _size(
        self,
        rets: np.ndarray,
        i: int,
        members: np.ndarray,
        p: AllocatorParams,
        eff_cap: float,
        k: int,
    ) -> np.ndarray:
        row = np.zeros(k)
        if len(members) == 0:
            return row
        if p.inverse_vol and i >= p.vol_lookback:
            vol = np.array([rets[i - p.vol_lookback + 1 : i + 1, j].std() or 1e-9 for j in members])
            ww = (1.0 / vol) / (1.0 / vol).sum()
        else:
            ww = np.ones(len(members)) / len(members)
        row[members] = ww * eff_cap
        return row

    def weight_path(self, close, *, p, cap_series=None):
        n, k = close.shape
        rets = np.vstack([np.zeros(k), close[1:] / close[:-1] - 1.0])
        oversold = self._oversold(close)
        w = np.zeros((n, k))
        cur = np.zeros(k)
        for i in range(n):
            if i % p.rebal_bars == 0:
                eff_cap = p.deploy_cap if cap_series is None else float(cap_series[i])
                cur = self._size(rets, i, np.where(oversold[i])[0], p, eff_cap, k)
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
        members = np.where(self._oversold(sub_r)[i])[0]
        row = self._size(rets_r, i, members, p, cap, sub_r.shape[1])
        weights = {c: 0.0 for c in cols}
        weights.update({c: float(row[j]) for j, c in enumerate(rank_cols)})
        return WeightDecision(weights, score, cap)

    def default_params(self):
        return AllocatorParams()

    def warmup(self, p):
        return max(self.window, p.vol_lookback) + 1

    def summary(self, p, *, n_tokens):
        return (
            f"Mean-reversion: buy {n_tokens}-token names trading >{self.threshold}σ below their "
            f"{self.window}-bar mean, inverse-vol, regime-capped. Forward-gated capability arm with "
            f"an adverse prior (reversal flips to momentum on majors) — promote with extra skepticism."
        )
