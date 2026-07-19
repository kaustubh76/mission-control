"""
Composable strategy overlays.

An overlay is a *de-risking* transform on a base strategy's output: it may only
contract deployment (zero or scale weights down), never lever up — spot is long-only
and `portfolio_replay.simulate` does not assert `sum(w) <= 1`, so a renormalize-up
overlay would silently imply leverage. The CONTRACTION INVARIANT
`sum(row_after) <= sum(row_before)` is the rule every overlay must satisfy (asserted
in tests/test_overlays.py).

`OverlayStrategy` wraps a base `PortfolioStrategy` + an ordered list of overlays into
a new registered strategy. With an `IdentityOverlay`, the result is bit-for-bit the
base — the same regression guarantee the registry already gives the locked path.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from ictbot.strategy.registry import StratContext, WeightDecision


@runtime_checkable
class Overlay(Protocol):
    name: str

    def apply_path(self, weight_path: np.ndarray, close: np.ndarray, *, p) -> np.ndarray:
        """Transform a full (n, k) weight path (backtest). Must be causal + a contraction."""
        ...

    def apply_now(
        self,
        weights: dict[str, float],
        *,
        close_df: pd.DataFrame,
        cap: float | None,
        ctx: StratContext,
    ) -> tuple[dict[str, float], float | None]:
        """Transform the live last-bar weights + the cap the base acted on."""
        ...

    def warmup(self, p) -> int:
        """Extra warmup bars the overlay needs (0 if none)."""
        ...

    def summary(self) -> str: ...


class OverlayStrategy:
    """A PortfolioStrategy = a base strategy + an ordered list of overlays applied after it."""

    def __init__(self, base, overlays: list[Overlay], *, name: str):
        self.base = base
        self.overlays = list(overlays)
        self.name = name

    def weight_path(self, close, *, p, cap_series=None):
        wp = self.base.weight_path(close, p=p, cap_series=cap_series)
        for ov in self.overlays:
            wp = ov.apply_path(wp, close, p=p)
        return wp

    def target_weights_now(self, close_df, *, ctx) -> WeightDecision:
        d = self.base.target_weights_now(close_df, ctx=ctx)
        weights, cap = d.weights, d.cap
        for ov in self.overlays:
            weights, cap = ov.apply_now(weights, close_df=close_df, cap=cap, ctx=ctx)
        return WeightDecision(weights, d.score, cap)

    def default_params(self):
        return self.base.default_params()

    def warmup(self, p):
        return max(self.base.warmup(p), *(ov.warmup(p) for ov in self.overlays), 0)

    def summary(self, p, *, n_tokens):
        tail = " → ".join(ov.summary() for ov in self.overlays)
        return f"{self.base.summary(p, n_tokens=n_tokens)} | overlay: {tail}"


class IdentityOverlay:
    """A no-op overlay — used to prove `OverlayStrategy(base, [Identity])` == base bit-for-bit."""

    name = "identity"

    def apply_path(self, weight_path, close, *, p):
        return weight_path

    def apply_now(self, weights, *, close_df, cap, ctx):
        return weights, cap

    def warmup(self, p):
        return 0

    def summary(self):
        return "identity"
