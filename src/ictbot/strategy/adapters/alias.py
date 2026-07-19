"""
Branded strategy aliases — contest-facing names (BNB_STRATEGY_0X) over the arms.

`AliasStrategy` delegates EVERY PortfolioStrategy method to a target registered
strategy but reports its own (branded) name. This lets a stable contest naming queue
ride on top of the descriptive arms without duplicating any logic — the alias is
bit-for-bit the arm it points at (see tests/test_strategy_aliases.py). Reassign the
queue in `registry.CONTEST_ALIASES` as forward testing picks a winner.
"""

from __future__ import annotations


class AliasStrategy:
    """A branded alias that delegates to a target PortfolioStrategy instance."""

    def __init__(self, name: str, target):
        self.name = name
        self._target = target

    @property
    def alias_of(self) -> str:
        return self._target.name

    def target_weights_now(self, close_df, *, ctx):
        return self._target.target_weights_now(close_df, ctx=ctx)

    def weight_path(self, close, *, p, cap_series=None):
        return self._target.weight_path(close, p=p, cap_series=cap_series)

    def default_params(self):
        return self._target.default_params()

    def warmup(self, p):
        return self._target.warmup(p)

    def summary(self, p, *, n_tokens):
        return f"{self.name} (alias of {self._target.name}) — {self._target.summary(p, n_tokens=n_tokens)}"
