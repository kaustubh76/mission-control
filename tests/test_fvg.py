from ictbot.indicators.fvg import get_micro_fvg


def test_bullish_fvg_detected(bullish_fvg_df):
    assert get_micro_fvg(bullish_fvg_df, "BULLISH") == "BULLISH FVG"


def test_bearish_fvg_detected(bearish_fvg_df):
    assert get_micro_fvg(bearish_fvg_df, "BEARISH") == "BEARISH FVG"


def test_no_fvg_on_flat(flat_df):
    assert get_micro_fvg(flat_df, "BULLISH") == "NO FVG"
    assert get_micro_fvg(flat_df, "BEARISH") == "NO FVG"


def test_short_frame_returns_no_fvg():
    import pandas as pd

    short = pd.DataFrame(
        {
            "open": [1, 1],
            "high": [2, 2],
            "low": [0, 0],
            "close": [1, 1],
            "volume": [1, 1],
        }
    )
    assert get_micro_fvg(short, "BULLISH") == "NO FVG"
