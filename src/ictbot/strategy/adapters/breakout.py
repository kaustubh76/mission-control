"""
Volatility-breakout strategy — Donchian breakout as a TARGET-WEIGHT BOOK.

A Donchian breakout is natively a per-pair entry+stop; the portfolio backtester has
no brackets (it consumes only a target-weight matrix). So membership in the held set
*is* the position:
  - ENTER token j when close[i] > its entry-channel high (donchian_upper, p_entry)
  - EXIT  token j when close[i] < its exit-channel low   (donchian_lower, p_exit)
  - else hold previous state (a stateful membership walk).

The exit channel is SHORTER than the entry channel (asymmetric, default entry 20 / exit 5,
12h rebalance) — the faster exit replaces the absent AMM stop, flattening a loser before the
next rebalance. (The exit5/rb3 default was chosen by the stability sweep; entry20/exit10/rb6
was UNSTABLE. See docs/bnb_strategy_decision.md.)
The held set is inverse-vol weighted and scaled by the regime cap (so `sum(row) ≤ cap`
keeps it DQ-safe under the 25% ceiling). Only the membership state machine is new;
the inverse-vol sizing + rebalance cadence are copied from momentum_allocator.

Turnover is the PASS/FAIL driver (bursty entries/exits bleed friction at 0.70% RT, and
quiet weeks can fall below the 7-trade floor) — sweep (p_entry, p_exit, rebal_bars) on
SIM, never hand-fit. A forward-gated capability arm (uniform promotion policy:
scripts/validate_strategy.py) — not an edge claim.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.indicators.channels import donchian_lower_series, donchian_upper_series
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.regime_score import adaptive_cap, regime_score
from ictbot.strategy.registry import StratContext, WeightDecision


class BreakoutStrategy:
    """Donchian channel breakout, expressed as a regime-capped inverse-vol target book."""

    name = "breakout"

    def __init__(self, entry_lb: int = 20, exit_lb: int = 5):
        self.entry_lb = entry_lb
        self.exit_lb = exit_lb

    def for_daily(self) -> BreakoutStrategy:
        """A DAILY-grid coarsening of the Donchian windows (6 x 4h = 1 day) for the CEX-free
        cmc_daily survival backtest. A 20-bar/5-bar channel on 4h ≈ a ~3-day/1-day channel on
        daily — coarser (conservative). The LIVE arm runs the true 4h windows on cmc_4h."""
        return BreakoutStrategy(
            entry_lb=max(2, round(self.entry_lb / 6)), exit_lb=max(2, round(self.exit_lb / 6))
        )

    def _membership(self, close: np.ndarray) -> np.ndarray:
        """(n, k) bool: is token j 'in' (broke out and not yet stopped) at bar i."""
        n, k = close.shape
        up = np.column_stack([donchian_upper_series(close[:, j], self.entry_lb) for j in range(k)])
        dn = np.column_stack([donchian_lower_series(close[:, j], self.exit_lb) for j in range(k)])
        in_set = np.zeros((n, k), dtype=bool)
        state = np.zeros(k, dtype=bool)
        for i in range(n):
            enter = close[i] > up[i]  # NaN (warmup) -> False
            exit_ = close[i] < dn[i]
            state = np.where(enter, True, np.where(exit_, False, state))
            in_set[i] = state
        return in_set

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
        in_set = self._membership(close)
        w = np.zeros((n, k))
        cur = np.zeros(k)
        for i in range(n):
            if i % p.rebal_bars == 0:
                eff_cap = p.deploy_cap if cap_series is None else float(cap_series[i])
                cur = self._size(rets, i, np.where(in_set[i])[0], p, eff_cap, k)
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
        members = np.where(self._membership(sub_r)[i])[0]
        row = self._size(rets_r, i, members, p, cap, sub_r.shape[1])
        weights = {c: 0.0 for c in cols}
        weights.update({c: float(row[j]) for j, c in enumerate(rank_cols)})
        return WeightDecision(weights, score, cap)

    def default_params(self):
        # Re-registered 2026-06-13: a 5-bar exit + 12h rebalance is ROBUST (stability sweep)
        # vs the original 10-bar exit / daily rebalance, which was UNSTABLE. A stability fix,
        # not an edge claim. See docs/bnb_strategy_decision.md.
        return AllocatorParams(rebal_bars=3)

    def warmup(self, p):
        return max(self.entry_lb, self.exit_lb, p.vol_lookback) + 1

    def summary(self, p, *, n_tokens):
        return (
            f"Volatility breakout: hold the {n_tokens}-token names that break their "
            f"{self.entry_lb}-bar high (exit on the {self.exit_lb}-bar low), inverse-vol, "
            f"regime-capped. Asymmetric exit channel replaces the AMM stop."
        )
