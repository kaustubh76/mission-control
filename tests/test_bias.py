from ictbot.indicators.bias_sma import get_htf_bias, get_ltf_bias


def test_htf_bias_bullish(bullish_df):
    assert get_htf_bias(bullish_df) == "BULLISH"


def test_htf_bias_bearish(bearish_df):
    assert get_htf_bias(bearish_df) == "BEARISH"


def test_ltf_bias_bullish(bullish_df):
    assert get_ltf_bias(bullish_df) == "BULLISH"


def test_ltf_bias_bearish(bearish_df):
    assert get_ltf_bias(bearish_df) == "BEARISH"


def test_flat_falls_through_to_one_label(flat_df):
    # Flat data — SMAs equal; ties resolve to BEARISH (sma20 > sma50 is False).
    # The contract is "returns one of the two labels", not a specific tie-break.
    assert get_htf_bias(flat_df) in ("BULLISH", "BEARISH")
    assert get_ltf_bias(flat_df) in ("BULLISH", "BEARISH")
