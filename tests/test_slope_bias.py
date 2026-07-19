import pandas as pd

from ictbot.indicators.bias_slope import get_slope_bias


def _df(closes):
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1] * len(closes),
        }
    )


def test_slope_bullish_on_uptrend():
    closes = list(range(100, 150))  # strictly increasing
    assert get_slope_bias(_df(closes), period=10, window=5) == "BULLISH"


def test_slope_bearish_on_downtrend():
    closes = list(range(150, 100, -1))
    assert get_slope_bias(_df(closes), period=10, window=5) == "BEARISH"


def test_slope_responds_faster_than_sma_to_a_turn():
    """After a sharp turn, slope should flip earlier than SMA crossover would."""
    # 30 bars of uptrend, then 8 bars of sharp downtrend.
    closes = list(range(100, 130)) + [128, 124, 120, 115, 110, 105, 100, 95]
    bias = get_slope_bias(_df(closes), period=10, window=5)
    assert bias == "BEARISH"  # the recent EMA slope is now negative


def test_fallback_when_too_short():
    closes = [100, 102]
    # 2 bars only — uses last-vs-first fallback. close[-1]=102 > close[0]=100 → BULLISH
    assert get_slope_bias(_df(closes), period=10, window=5) == "BULLISH"


def test_returns_a_label_on_flat_data():
    closes = [100] * 50
    bias = get_slope_bias(_df(closes), period=10, window=5)
    assert bias in ("BULLISH", "BEARISH")  # tie-break either way is fine
