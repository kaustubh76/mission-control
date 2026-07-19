from ictbot.indicators.mss import get_ltf_mss


def test_bullish_mss_detected(bullish_mss_df):
    assert get_ltf_mss(bullish_mss_df, "BULLISH") == "BULLISH MSS"


def test_bearish_mss_detected(bearish_mss_df):
    assert get_ltf_mss(bearish_mss_df, "BEARISH") == "BEARISH MSS"


def test_no_mss_when_bias_opposite(bullish_mss_df):
    # Higher high formed, but bias is BEARISH → should NOT call it BULLISH MSS.
    assert get_ltf_mss(bullish_mss_df, "BEARISH") == "NO MSS"


def test_no_mss_on_flat(flat_df):
    assert get_ltf_mss(flat_df, "BULLISH") == "NO MSS"
    assert get_ltf_mss(flat_df, "BEARISH") == "NO MSS"
