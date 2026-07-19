"""
Align DAILY CMC macro (BTC dominance, total market cap, Fear & Greed) onto the 4h
candle index for the backtest A/B — PURE, no network.

The clean A/B holds the momentum engine + candles CONSTANT and varies only the deploy-
cap source. The macro is daily; the backtest runs on 4h bars. So forward-fill each
day's macro onto its 4h bars and attach the ~30-day-ago baselines (for the dominance/
mktcap trend terms) + the trailing 7-day F&G average — all with NO lookahead:
  - ffill = most-recent PRIOR daily value (each bar sees only its day's already-printed macro),
  - `_prev` = a backward shift on the daily series,
  - `fng_7d` = a trailing rolling mean.
Bars before the macro series begins (or before the 30-day baseline is defined) get NaN;
`cap_series_enhanced` treats NaN terms as absent → those bars reduce to the baseline cap,
which is exactly the warmup region `evaluate` already discards.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AlignedMacro:
    dominance: np.ndarray  # (n,) per-4h-bar BTC dominance %, ffilled from daily
    dominance_prev: np.ndarray  # (n,) dominance ~`prev_days` ago
    mktcap: np.ndarray  # (n,) total market cap USD
    mktcap_prev: np.ndarray  # (n,) total market cap ~`prev_days` ago
    fng: np.ndarray  # (n,) Fear & Greed level
    fng_7d: np.ndarray  # (n,) trailing 7-day F&G average

    def any_present(self) -> bool:
        return any(np.isfinite(a).any() for a in (self.dominance, self.mktcap, self.fng))


def _daily_frame(rows: list[dict]) -> pd.DataFrame | None:
    """rows = [{ts:int(epoch_s), ...}] → frame indexed by UTC day (unique, sorted)."""
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["day"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.floor("D")
    return df.drop_duplicates("day", keep="last").set_index("day").sort_index()


def align_macro_to_index(
    index,
    gm_hist: list[dict],
    fng_hist: list[dict],
    *,
    prev_days: int = 30,
    fng_avg_days: int = 7,
) -> AlignedMacro:
    """Forward-fill daily macro onto the 4h candle `index` (the close-matrix index).

    `gm_hist` = global_metrics_history() rows, `fng_hist` = fng_history() rows. Returns
    per-bar arrays of length len(index); NaN where macro is absent (→ baseline)."""
    idx = pd.DatetimeIndex(index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    n = len(idx)
    bar_day = idx.floor("D")

    def _nan() -> np.ndarray:
        return np.full(n, np.nan)

    def _ffill_to_bars(daily_df: pd.DataFrame | None, col: str) -> np.ndarray:
        """Reindex a daily column onto EVERY calendar day (ffill the gaps), then map
        each 4h bar to its day. Bars before the column's first value stay NaN."""
        if daily_df is None or col not in daily_df.columns or n == 0:
            return _nan()
        s = daily_df[col].dropna()
        if s.empty:
            return _nan()
        full = pd.date_range(s.index.min(), bar_day.max(), freq="D", tz="UTC")
        s_full = s.reindex(full).ffill()
        return s_full.reindex(bar_day).to_numpy(dtype=float)

    gm = _daily_frame(gm_hist)
    if gm is not None:
        gm["dom_prev"] = gm["btc_dominance"].shift(prev_days)  # ~30 daily rows ≈ 30 days
        gm["mc_prev"] = gm["total_market_cap"].shift(prev_days)

    fng = _daily_frame(fng_hist)
    if fng is not None:
        fng["fng_7d"] = fng["value"].rolling(fng_avg_days, min_periods=1).mean()

    return AlignedMacro(
        dominance=_ffill_to_bars(gm, "btc_dominance"),
        dominance_prev=_ffill_to_bars(gm, "dom_prev"),
        mktcap=_ffill_to_bars(gm, "total_market_cap"),
        mktcap_prev=_ffill_to_bars(gm, "mc_prev"),
        fng=_ffill_to_bars(fng, "value"),
        fng_7d=_ffill_to_bars(fng, "fng_7d"),
    )
