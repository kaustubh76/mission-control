"""
CMC-style technical analysis — RSI / MACD / EMA on the DAILY timeframe, PURE (no network).

The CMC Agent Hub's `get_crypto_technical_analysis` tool returns DAILY indicators
(rsi7/14/21, macd line/signal/histogram, sma+ema 7/30/200, fib, pivot). We mirror the
same daily computation locally so ONE signal is interchangeable: the backtest A/B uses
these locally-computed values on the candle history, while LIVE reads CMC's authoritative
pre-computed version (`cmc_agent_hub.technical_analysis`) — offloading the compute to CMC.

Everything here is causal (Wilder RSI, EMA-of-EMA MACD, trailing EMAs) → no lookahead.
Two allocator-facing signals are derived from the per-token daily TA:
  - `trend_health(daily_close)` → (n,) basket risk-on score in [0,1] for the DEPLOY CAP
    (breadth of MACD-bull + above-EMA tokens, braked when the basket is overbought),
  - `token_ta_score(daily_close)` → (n, k) per-token momentum CONFIRMATION in [0,1] for
    the RANKING (positive-MACD + healthy-RSI + above-EMA, penalised when overbought).

For the backtest, daily TA is resampled from the 4h close matrix and forward-filled back
onto the 4h bar index with `align_daily_to_index` (the macro_align pattern, no lookahead).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Defaults match CMC's daily TA tool (rsi14, macd 12/26/9, ema 30 as the trend MA).
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
EMA_TREND = 30
_RSI_OVERBOUGHT = 70.0


def _as_df(close) -> pd.DataFrame:
    """Accept a 2D array / DataFrame of daily closes (rows=days, cols=tokens) → DataFrame."""
    if isinstance(close, pd.DataFrame):
        return close.astype(float)
    arr = np.asarray(close, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    return pd.DataFrame(arr)


def ema(close, span: int) -> pd.DataFrame:
    """Causal EMA (adjust=False), per column. NaN until the column has a value."""
    return _as_df(close).ewm(span=span, adjust=False, min_periods=1).mean()


def rsi(close, period: int = RSI_PERIOD) -> pd.DataFrame:
    """Wilder's RSI per column, in [0, 100]. NaN for the first `period` rows (warmup)."""
    df = _as_df(close)
    delta = df.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder smoothing == EMA with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0.0, 100.0)  # all-gains window → RSI 100
    return out.mask(avg_gain.isna())  # keep the warmup NaNs


def macd(close, fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL):
    """Return (macd_line, signal_line, histogram) DataFrames (line = EMAfast − EMAslow)."""
    df = _as_df(close)
    macd_line = ema(df, fast) - ema(df, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=1).mean()
    return macd_line, signal_line, macd_line - signal_line


def trend_health(
    daily_close,
    *,
    ema_trend: int = EMA_TREND,
    rsi_period: int = RSI_PERIOD,
    overbought: float = _RSI_OVERBOUGHT,
) -> np.ndarray:
    """Per-bar basket TREND-HEALTH in [0,1] for the deploy cap (high = risk-on uptrend).

    health = mean(MACD-bull breadth, above-EMA breadth) − overbought brake. Rows with no
    finite TA (warmup) return NaN so `cap_series_enhanced` treats them as absent (→ baseline).
    """
    df = _as_df(daily_close)
    _, _, hist = macd(df)
    above = df > ema(df, ema_trend)
    rsi14 = rsi(df, rsi_period)

    macd_bull = hist > 0
    valid = hist.notna() & rsi14.notna()
    n_valid = valid.sum(axis=1)

    bull_frac = (macd_bull & valid).sum(axis=1) / n_valid.replace(0, np.nan)
    above_frac = (above & valid).sum(axis=1) / n_valid.replace(0, np.nan)
    ob_frac = ((rsi14 > overbought) & valid).sum(axis=1) / n_valid.replace(0, np.nan)

    health = 0.5 * bull_frac + 0.5 * above_frac - 0.5 * (ob_frac - 0.30).clip(lower=0.0)
    return health.clip(0.0, 1.0).to_numpy(dtype=float)


def token_ta_score(
    daily_close,
    *,
    ema_trend: int = EMA_TREND,
    rsi_period: int = RSI_PERIOD,
    overbought: float = _RSI_OVERBOUGHT,
) -> np.ndarray:
    """Per-token momentum CONFIRMATION in [0,1] (n, k) for the ranking — rewards positive
    MACD + above-trend + healthy (non-overbought) RSI. NaN during warmup (→ neutral 1.0
    handled by the caller). Centred so 0.5 is neutral; reduces variance, not direction."""
    df = _as_df(daily_close)
    _, _, hist = macd(df)
    above = (df > ema(df, ema_trend)).astype(float)
    rsi14 = rsi(df, rsi_period)

    macd_pos = (hist > 0).astype(float)
    # RSI health: 1.0 in the trend band [50,70], decaying below 50 and above 70.
    rsi_health = (1.0 - (rsi14 - 60.0).abs() / 40.0).clip(0.0, 1.0)
    ob_pen = (rsi14 > overbought).astype(float)

    score = 0.40 * macd_pos + 0.35 * above + 0.25 * rsi_health - 0.20 * ob_pen
    score = score.clip(0.0, 1.0)
    return score.mask(hist.isna() | rsi14.isna()).to_numpy(dtype=float)


# --------------------------------------------------------------------------- #
# Daily resample + 4h alignment for the backtest (macro_align pattern, no lookahead)
# --------------------------------------------------------------------------- #
def resample_daily(close4h: pd.DataFrame) -> pd.DataFrame:
    """Resample a 4h close matrix (DatetimeIndex, cols=tokens) to the last close per UTC
    day. Causal: each daily bar is that day's final printed 4h close."""
    idx = pd.DatetimeIndex(close4h.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df = close4h.copy()
    df.index = idx
    return df.resample("1D").last().dropna(how="all")


def align_daily_to_index(daily_vals: np.ndarray, daily_index, target_index) -> np.ndarray:
    """Forward-fill a daily TA array (n_days,) or (n_days, k) onto the 4h `target_index`.

    Mirrors macro_align: reindex onto every calendar day (ffill the gaps), map each 4h bar
    to its day. Bars before the first daily value stay NaN (→ baseline). No lookahead."""
    tgt = pd.DatetimeIndex(target_index)
    tgt = tgt.tz_localize("UTC") if tgt.tz is None else tgt.tz_convert("UTC")
    bar_day = tgt.floor("D")
    day_idx = pd.DatetimeIndex(daily_index)
    day_idx = day_idx.tz_localize("UTC") if day_idx.tz is None else day_idx.tz_convert("UTC")
    day_idx = day_idx.floor("D")

    vals = np.asarray(daily_vals, dtype=float)
    cols = 1 if vals.ndim == 1 else vals.shape[1]
    df = pd.DataFrame(vals.reshape(len(day_idx), cols), index=day_idx)
    df = df[~df.index.duplicated(keep="last")].sort_index()

    full = pd.date_range(df.index.min(), bar_day.max(), freq="D", tz="UTC")
    out = df.reindex(full).ffill().reindex(bar_day).to_numpy(dtype=float)
    return out[:, 0] if vals.ndim == 1 else out
