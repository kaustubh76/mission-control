"""Unit tests for the trend-following signal (trend_basket)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.indicators.atr import get_atr
from ictbot.indicators.bias_sma import get_htf_bias
from ictbot.indicators.regime import atr_percentile_regime
from ictbot.strategy.trend_basket import (
    TrendBasketStrategy,
    TrendParams,
    base_features,
    compute_features,
    signal_at,
    trend_signal,
)


def make_df(closes: np.ndarray) -> pd.DataFrame:
    n = len(closes)
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="4h"),
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.ones(n),
        }
    )


def trend_with_pullbacks(n: int, slope: float, base: float = 100.0, amp: float = 3.0):
    i = np.arange(n)
    return base + slope * i + amp * np.sin(i * 2 * np.pi / 10.0)


def scan(df, p):
    feat = compute_features(df, p)
    return [s for i in range(len(df)) if (s := signal_at(feat, i)) is not None]


def test_uptrend_fires_long_with_valid_bracket():
    df = make_df(trend_with_pullbacks(200, slope=0.5))
    p = TrendParams(ma_window=20, slope_period=20, pullback_lookback=5, sl_atr=2.0, rr=2.0)
    sigs = scan(df, p)
    longs = [s for s in sigs if s["side"] == "BUY"]
    assert longs, "expected at least one long in a rising series with pullbacks"
    s = longs[0]
    assert s["sl"] < s["price"] < s["tp"]  # long bracket geometry
    assert s["rr"] >= 2.0  # RR floor enforced


def test_downtrend_fires_short_and_long_only_suppresses_it():
    df = make_df(trend_with_pullbacks(200, slope=-0.5, base=400.0))
    short_p = TrendParams(pullback_lookback=5, allow_short=True)
    shorts = [s for s in scan(df, short_p) if s["side"] == "SELL"]
    assert shorts, "expected at least one short in a falling series"
    s = shorts[0]
    assert s["tp"] < s["price"] < s["sl"]  # short bracket geometry

    lo_p = TrendParams(pullback_lookback=5, long_only=True, allow_short=False)
    assert not [s for s in scan(df, lo_p) if s["side"] == "SELL"]


def test_vectorised_features_match_indicator_functions():
    # need >= period+window (14+200) bars for regime to actually classify
    df = make_df(trend_with_pullbacks(320, slope=0.3))
    b = base_features(df)
    assert bool(b.sma_bull[-1]) == (get_htf_bias(df) == "BULLISH")
    assert bool(b.low_vol[-1]) == (atr_percentile_regime(df) == "LOW_VOL")
    assert abs(b.atr[-1] - get_atr(df)) < 1e-5


def test_trend_signal_dict_shape_and_strategy_adapter():
    df = make_df(trend_with_pullbacks(200, slope=0.5))
    out = trend_signal(df, TrendParams(pullback_lookback=5))
    for k in ("entry", "price", "sl", "tp", "rr", "confidence", "htf_bias", "error", "diagnostics"):
        assert k in out
    assert out["entry"] in ("BUY", "SELL", "NO ENTRY")

    # the live Strategy adapter reads entry_df and stamps the pair through
    strat = TrendBasketStrategy(TrendParams(pullback_lookback=5))
    res = strat.evaluate(df, df, df, df, session={}, pair="BTC/USDT")
    assert res["pair"] == "BTC/USDT"
    assert res["entry"] in ("BUY", "SELL", "NO ENTRY")


def test_short_or_insufficient_history_is_no_entry():
    df = make_df(np.linspace(100, 101, 30))  # < WARMUP
    out = trend_signal(df)
    assert out["entry"] == "NO ENTRY"
    assert out["error"] is not None
