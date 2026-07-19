import pandas as pd

from ictbot.indicators.poi_min_max import get_ltf_poi, get_poi_tap


def test_bullish_poi_is_recent_low():
    df = pd.DataFrame(
        {
            "open": [10] * 20,
            "high": [11] * 20,
            "low": [9] * 19 + [5],  # last 20 low = 5
            "close": [10] * 20,
            "volume": [1] * 20,
        }
    )
    assert get_ltf_poi(df, "BULLISH") == 5.0


def test_bearish_poi_is_recent_high():
    df = pd.DataFrame(
        {
            "open": [10] * 20,
            "high": [11] * 19 + [20],  # last 20 high = 20
            "low": [9] * 20,
            "close": [10] * 20,
            "volume": [1] * 20,
        }
    )
    assert get_ltf_poi(df, "BEARISH") == 20.0


def test_poi_tap_when_close():
    df = pd.DataFrame(
        {
            "open": [100],
            "high": [100.1],
            "low": [99.9],
            "close": [100.0],
            "volume": [1],
        }
    )
    # POI within 0.15% of 100 → 99.85..100.15 → 100.05 must tap
    assert get_poi_tap(df, 100.05) == "POI TAPPED"


def test_poi_tap_when_far():
    df = pd.DataFrame(
        {
            "open": [100],
            "high": [100.1],
            "low": [99.9],
            "close": [100.0],
            "volume": [1],
        }
    )
    assert get_poi_tap(df, 50) == "WAITING"
