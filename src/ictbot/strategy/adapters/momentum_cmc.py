"""
The CMC-driven contest arm — `momentum_cmc`.

The **CoinMarketCap-native** strategy: every input to the decision is CMC's own data — no exchange candles.
Token SELECTION ranks on **CMC 4h candles** that we accumulate ourselves from CMC's live WebSocket feed
(`scripts/cmc_stream.py` → `data.cmc.cmc_4h_close_matrix`), because CMC has no historical intraday OHLCV on
our tier. Sizing is the regime-adaptive deploy cap (breadth + trend + vol on the CMC matrix, + live CMC
Fear&Greed / market-overview risk-budget); the live A/B levers add CMC per-token TA confirmation + CMC 7d
%change tilt.

It reuses the LOCKED ranking/cap code verbatim (subclasses `AdaptiveMomentumStrategy`); it only swaps the
data SOURCE via `candle_source = "cmc_4h"`. Because CMC 4h closes ≈ exchange 4h closes, it runs the **proven
4h-native params** (the default `AllocatorParams`: lookback 120 = 20d, daily rebal, inverse-vol) — i.e. the
same risk profile as the locked `momentum_adaptive` default (~17.5% worst-week DD, DQ-safe), but sourced
entirely from CoinMarketCap. A forward-gated capability arm (uniform promotion policy); not an edge claim and
not the contest default until it clears the gate (survival + stability + forward) and an operator signs off.

Cold start: until the live stream has accumulated enough 4h bars, `cmc_4h_close_matrix` backfills the lookback
history from CMC daily closes forward-filled onto the 4h grid (still 100% CMC; momentum stays accurate).
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from ictbot.strategy.adapters.momentum import AdaptiveMomentumStrategy
from ictbot.strategy.momentum_allocator import cmc_seed_vol_floor
from ictbot.strategy.registry import StratContext, WeightDecision


class CMCMomentumStrategy(AdaptiveMomentumStrategy):
    """Regime-adaptive momentum on CMC's own 4h candles — the CMC-driven contest arm."""

    name = "momentum_cmc"
    candle_source = "cmc_4h"  # the per-arm seam in run_allocator/validate reads this

    # Inherits the locked ranking/cap code AND the 4h-native default params from
    # AdaptiveMomentumStrategy — identical machinery to momentum_adaptive, just fed CMC candles.

    def _vol_floor(self, close_df: pd.DataFrame) -> float:
        """Delegate to the shared cold-start seed floor (momentum_allocator.cmc_seed_vol_floor).
        run_allocator now injects the SAME floor for every cmc_4h arm; this override stays as
        idempotent belt-and-suspenders for the direct-call path (and the adapter test)."""
        return cmc_seed_vol_floor(close_df)

    def target_weights_now(self, close_df: pd.DataFrame, *, ctx: StratContext) -> WeightDecision:
        vf = self._vol_floor(close_df)
        if vf <= 0:
            return super().target_weights_now(close_df, ctx=ctx)
        return super().target_weights_now(
            close_df, ctx=replace(ctx, params=replace(ctx.params, vol_floor=vf))
        )

    # weight_path (the backtest) is left inherited: the cited DQ validation runs on real CMC
    # DAILY candles (no flat-intrabar seed → no vol collapse → vol_floor=0.0 is correct there).

    def summary(self, p, *, n_tokens: int) -> str:
        return (
            f"CMC-driven momentum: hold top-{p.top_k} of {n_tokens} by {p.lookback}-bar (20d) return "
            f"on CoinMarketCap's own 4h candles (inverse-vol, regime-adaptive cap), confirmed live by "
            f"CMC technical-analysis + Fear&Greed. The entire decision runs on CMC data."
        )
