"""
Trend-Following basket strategy (the validated-edge replacement for the
ICT entry stack).

The repo's own docs/findings.md §13–§15 showed the ICT POI/MSS/FVG entry has
*negative* out-of-sample expectancy after friction. The trend/bias layer is
separable and is the one crypto anomaly with peer-reviewed support
(Moskowitz-Ooi-Pedersen TSMOM; Liu-Tsyvinski; Man AHL "diversification is the
edge"). This module keeps ONLY that pure trend signal — no POI / MSS / FVG /
delta — plus a simple pullback-to-the-MA entry that buys the discount instead
of the breakout extension (friction is the dominant P&L term in this codebase,
so a tighter fill matters).

Signal (per coin, on closed 4h candles):
  direction  : SMA20>SMA50 (get_htf_bias) AND EMA-slope>0 (get_slope_bias) agree
  regime     : skip when atr_percentile_regime == LOW_VOL (chop)
  timing     : enter on a pullback to the bias-MA that the bar then reclaims
  stop/target: ATR-anchored, RR >= 2 floor

`compute_features` is a vectorised mirror of the per-bar indicator functions so
the walk-forward replay stays O(n) rather than O(n^2); `tests/test_trend_basket.py`
cross-checks the vectorised htf-bias / regime against the real indicator funcs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ictbot.strategy.base import Strategy

# Direction needs SMA50; slope needs ema span + window; regime ranks vs 200 bars.
# We only START emitting signals after this many bars so the indicators are real
# (matches the indicator funcs' own fallbacks rather than relying on them).
WARMUP = 60


@dataclass(frozen=True)
class TrendParams:
    ma_window: int = 20  # the bias-MA the pullback references
    slope_period: int = 20  # EMA span for the slope filter
    slope_window: int = 5  # bars the slope is measured over
    atr_period: int = 14
    sl_atr: float = 2.0  # stop distance = sl_atr * ATR
    rr: float = 2.0  # take-profit = rr * risk (RR floor)
    pullback_lookback: int = 3  # bars within which a pullback must have tagged the MA
    long_only: bool = False  # spot fallback config sets this True
    allow_short: bool = True


# --------------------------------------------------------------------------- #
# Vectorised feature computation
# --------------------------------------------------------------------------- #
@dataclass
class _Base:
    """Per-symbol fixed features (independent of the swept params)."""

    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    sma_bull: np.ndarray  # SMA20 > SMA50  (mirrors get_htf_bias)
    atr: np.ndarray  # ATR(14) series (mirrors get_atr at each bar)
    low_vol: np.ndarray  # atr_percentile_regime == LOW_VOL  (mirrors regime.py)


def _atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    prev_close = np.empty_like(close)
    prev_close[0] = np.nan
    prev_close[1:] = close[:-1]
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    return pd.Series(tr).rolling(period).mean().to_numpy()


def _low_vol_series(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
    window: int = 200,
    lo: float = 0.30,
) -> np.ndarray:
    """Mirror of indicators.regime.atr_percentile_regime == 'LOW_VOL'."""
    atr_s = pd.Series(_atr_series(high, low, close, period))
    out = np.zeros(len(close), dtype=bool)
    n = len(close)
    for i in range(period + window, n):
        recent = atr_s.iloc[i - window + 1 : i + 1].dropna()
        cur = atr_s.iloc[i]
        if recent.empty or not np.isfinite(cur) or cur <= 0:
            continue
        rank = float((recent <= cur).mean())  # ECDF, as in regime.py
        out[i] = rank <= lo
    return out


def base_features(df: pd.DataFrame, atr_period: int = 14) -> _Base:
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    s = df["close"]
    sma20 = s.rolling(20).mean().to_numpy()
    sma50 = s.rolling(50).mean().to_numpy()
    sma_bull = sma20 > sma50  # NaN > NaN -> False during warmup (treated bearish)
    atr = _atr_series(high, low, close, atr_period)
    low_vol = _low_vol_series(high, low, close)
    return _Base(close=close, high=high, low=low, sma_bull=sma_bull, atr=atr, low_vol=low_vol)


def _rolling_slope_positive(y: np.ndarray, window: int) -> np.ndarray:
    """Exact OLS-slope sign of `y` over a trailing `window` (mirrors get_slope_bias)."""
    n = len(y)
    out = np.zeros(n, dtype=bool)
    x = np.arange(window, dtype=float)
    xc = x - x.mean()
    denom = float((xc**2).sum())
    if denom == 0:
        return out
    for i in range(window - 1, n):
        win = y[i - window + 1 : i + 1]
        if np.isnan(win).any():
            continue
        num = float((xc * (win - win.mean())).sum())
        out[i] = (num / denom) > 0
    return out


@dataclass
class _Feat:
    base: _Base
    slope_bull: np.ndarray
    ma: np.ndarray
    min_low_k: np.ndarray
    max_high_k: np.ndarray
    p: TrendParams


def compute_features(df: pd.DataFrame, p: TrendParams, base: _Base | None = None) -> _Feat:
    if base is None:
        base = base_features(df, p.atr_period)
    s = df["close"]
    ema = s.ewm(span=p.slope_period, adjust=False).mean().to_numpy()
    slope_bull = _rolling_slope_positive(ema, p.slope_window)
    ma = s.rolling(p.ma_window).mean().to_numpy()
    min_low_k = df["low"].rolling(p.pullback_lookback).min().to_numpy()
    max_high_k = df["high"].rolling(p.pullback_lookback).max().to_numpy()
    return _Feat(
        base=base, slope_bull=slope_bull, ma=ma, min_low_k=min_low_k, max_high_k=max_high_k, p=p
    )


def signal_at(feat: _Feat, i: int) -> dict | None:
    """Return an entry dict for bar `i`, or None for NO ENTRY.

    dict keys: side ('BUY'|'SELL'), price, sl, tp, rr.
    """
    if i < WARMUP:
        return None
    b, p = feat.base, feat.p
    ma_i = feat.ma[i]
    atr_i = b.atr[i]
    close_i = b.close[i]
    if not np.isfinite(ma_i) or not np.isfinite(atr_i) or atr_i <= 0:
        return None
    if b.low_vol[i]:
        return None

    htf_bull = bool(b.sma_bull[i])
    slope_bull = bool(feat.slope_bull[i])

    side = None
    if htf_bull and slope_bull:
        side = "BUY"
    elif (not htf_bull) and (not slope_bull) and p.allow_short and not p.long_only:
        side = "SELL"
    if side is None:
        return None

    if side == "BUY":
        # pullback: price dipped to/under the MA within lookback, now reclaims it.
        if not (feat.min_low_k[i] <= ma_i and close_i > ma_i):
            return None
        risk = p.sl_atr * atr_i
        sl = close_i - risk
        tp = close_i + p.rr * risk
    else:  # SELL
        if not (feat.max_high_k[i] >= ma_i and close_i < ma_i):
            return None
        risk = p.sl_atr * atr_i
        sl = close_i + risk
        tp = close_i - p.rr * risk

    if risk <= 0:
        return None
    rr = abs(tp - close_i) / abs(close_i - sl)
    if rr < 2.0:
        return None
    return {
        "side": side,
        "price": float(close_i),
        "sl": float(sl),
        "tp": float(tp),
        "rr": float(rr),
    }


# --------------------------------------------------------------------------- #
# Single-frame convenience + live Strategy adapter
# --------------------------------------------------------------------------- #
def trend_signal(df: pd.DataFrame, p: TrendParams | None = None) -> dict:
    """Evaluate the LAST bar of `df`. Returns the canonical result dict."""
    p = p or TrendParams()
    if df is None or len(df) < WARMUP:
        return _empty(df, "insufficient bars")
    feat = compute_features(df, p)
    i = len(df) - 1
    sig = signal_at(feat, i)
    htf = "BULLISH" if bool(feat.base.sma_bull[i]) else "BEARISH"
    if sig is None:
        return _empty(df, None, entry="NO ENTRY", htf_bias=htf, price=float(feat.base.close[i]))
    return {
        "pair": "TEST",
        "error": None,
        "entry": "BUY" if sig["side"] == "BUY" else "SELL",
        "price": sig["price"],
        "sl": sig["sl"],
        "tp": sig["tp"],
        "rr": sig["rr"],
        "confidence": 75,
        "htf_bias": htf,
        "ltf_bias": htf,
        "atr_1m": float(feat.base.atr[i]),
        "regime": "LOW_VOL" if bool(feat.base.low_vol[i]) else "NORMAL",
        "diagnostics": {"near_miss": False, "closest_direction": sig["side"], "blockers": []},
    }


def _empty(df, error, *, entry="NO ENTRY", htf_bias="N/A", price=0.0) -> dict:
    return {
        "pair": "TEST",
        "error": error,
        "entry": entry,
        "price": price,
        "sl": 0.0,
        "tp": 0.0,
        "rr": 0.0,
        "confidence": 0,
        "htf_bias": htf_bias,
        "ltf_bias": htf_bias,
        "atr_1m": 0.0,
        "regime": None,
        "diagnostics": {
            "near_miss": False,
            "closest_direction": "NONE",
            "blockers": [error or "no setup"],
        },
    }


class TrendBasketStrategy(Strategy):
    """Live adapter: decisions are made on the entry frame (the 4h candles).

    The scanner passes (htf_df, bias_df, poi_df, entry_df, session, pair); a
    trend strategy only needs one frame, so it reads `entry_df`. Configure the
    contest's spot-1x fallback with `TrendParams(long_only=True)`.
    """

    def __init__(self, params: TrendParams | None = None) -> None:
        self.params = params or TrendParams()

    def evaluate(self, htf_df, bias_df, poi_df, entry_df, session, pair: str = "TEST") -> dict:
        out = trend_signal(entry_df, self.params)
        out["pair"] = pair
        return out
