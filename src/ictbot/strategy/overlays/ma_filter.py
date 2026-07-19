"""
Moving-average trend filter overlay.

Per-token risk-off gate: zero a token's weight whenever its close is below its own
SMA(window). A pure filter — it does NOT renormalize the survivors (deployment is
allowed to fall, exactly like the allocator's per-token cash filter), so it only ever
contracts the book. NaN during warmup compares False (no zeroing), the same
convention as `trend_basket.base_features`.

Note: in a basket-wide downtrend every token falls below its MA, so this collapses to
~all-cash — i.e. it behaves like `dual_momentum`'s risk-off, not orthogonal alpha. It
is another risk-off LENS (findings.md disproved MA-crossover as a standalone edge); we
ship it as a filter only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy.momentum_allocator import CONTEST_TOKENS


class MaFilterOverlay:
    """Zero any token trading below its own SMA(window). De-risk only."""

    name = "ma_filter"

    def __init__(self, window: int = 50):
        self.window = window

    def apply_path(self, weight_path: np.ndarray, close: np.ndarray, *, p) -> np.ndarray:
        sma = pd.DataFrame(close).rolling(self.window).mean().to_numpy()
        below = close < sma  # NaN comparison -> False during warmup (no zeroing)
        out = weight_path.copy()
        out[below] = 0.0
        return out

    def apply_now(self, weights, *, close_df, cap, ctx):
        out = dict(weights)
        for c in list(out.keys()):
            if c not in CONTEST_TOKENS or c not in close_df.columns:
                continue
            col = close_df[c].dropna()
            if len(col) < self.window:
                continue
            sma = float(col.iloc[-self.window :].mean())
            if float(col.iloc[-1]) < sma:
                out[c] = 0.0
        return out, cap  # cap unchanged; realized deployment simply drops

    def warmup(self, p) -> int:
        return self.window

    def summary(self) -> str:
        return f"MA({self.window}) trend filter"
