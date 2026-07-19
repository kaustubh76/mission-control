"""
Volatility-targeting overlay.

Scales total DEPLOYMENT inverse to the basket's realized volatility, targeting a
constant book vol — `s_i = clamp(target_vol / max(realized_vol, eps), 0, 1)`. The
clamp at 1.0 is the hard rule: spot is long-only, so the overlay can only de-risk in
high-vol regimes, never lever up in calm ones. It is ORTHOGONAL to the regime cap's
own `_vol_factor` brake (a coarse 0.6/1.0 ECDF step already inside the base cap) —
this adds a continuous book-vol target on top.

Realized vol = trailing std of the equal-weight basket index returns
(`regime_score.index_series` + `bands.rolling_std_series`). With `target_vol` set
high enough that `s ≡ 1`, the overlay is bit-for-bit the base (tested).
"""

from __future__ import annotations

import numpy as np

from ictbot.indicators.bands import rolling_std_series
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS
from ictbot.strategy.regime_score import index_series

_EPS = 1e-9


class VolTargetOverlay:
    """De-risk toward a constant basket-return volatility; never levers up (clamp ≤ 1)."""

    name = "vol_target"

    def __init__(self, target_vol: float = 0.012, vol_lookback: int = 30):
        # target_vol is a per-4h-bar basket-return std target (~1.2% default). When the
        # basket runs hotter than this the book is scaled down pro-rata; calmer -> s=1.
        self.target_vol = target_vol
        self.vol_lookback = vol_lookback

    def _scalars(self, close: np.ndarray) -> np.ndarray:
        """Per-bar deployment scalar s_i in [0, 1] (1.0 during warmup / when undefined)."""
        idx = index_series(close)
        rets = np.zeros_like(idx)
        rets[1:] = idx[1:] / idx[:-1] - 1.0
        rv = rolling_std_series(rets, self.vol_lookback)
        s = np.clip(self.target_vol / np.maximum(rv, _EPS), 0.0, 1.0)
        s[~np.isfinite(s)] = 1.0  # warmup / undefined vol -> identity (no scaling)
        return s

    def apply_path(self, weight_path: np.ndarray, close: np.ndarray, *, p) -> np.ndarray:
        s = self._scalars(close)
        return weight_path * s[:, None]  # scale every row total (cash absorbs the rest)

    def apply_now(self, weights, *, close_df, cap, ctx):
        cols = [c for c in close_df.columns if c in CONTEST_TOKENS]
        sub = close_df[cols].dropna()
        if len(sub) <= self.vol_lookback:
            return weights, cap
        s = float(self._scalars(sub.to_numpy(dtype=float))[-1])
        w2 = {k: v * s for k, v in weights.items()}
        return w2, (None if cap is None else cap * s)

    def warmup(self, p) -> int:
        return self.vol_lookback + 1

    def summary(self) -> str:
        return f"vol-target {self.target_vol:.1%}/bar (de-risk only)"
