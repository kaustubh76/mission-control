"""
Tests for analyzer._diagnose — explains which conditions block BUY/SELL.

The v2 entry rule (HTF-direction) means LTF bias is no longer a blocker.
"""

from ictbot.orchestrator.analyzer import _diagnose


def test_full_bullish_setup_has_no_buy_blockers():
    diag = _diagnose(
        htf_bias="BULLISH",
        poi_tap="POI TAPPED",
        ltf_mss="BULLISH MSS",
        micro_fvg="BULLISH FVG",
        delta=100,
    )
    assert diag["buy_blockers"] == []
    assert diag["closest_direction"] == "BUY"
    assert diag["near_miss"] is False  # full setup, not "near"


def test_full_bearish_setup_has_no_sell_blockers():
    diag = _diagnose(
        htf_bias="BEARISH",
        poi_tap="POI TAPPED",
        ltf_mss="BEARISH MSS",
        micro_fvg="BEARISH FVG",
        delta=-100,
    )
    assert diag["sell_blockers"] == []
    assert diag["closest_direction"] == "SELL"


def test_near_miss_is_one_blocker():
    """All conditions met except FVG — should be flagged as near-miss."""
    diag = _diagnose(
        htf_bias="BULLISH",
        poi_tap="POI TAPPED",
        ltf_mss="BULLISH MSS",
        micro_fvg="NO FVG",
        delta=100,
    )
    assert len(diag["buy_blockers"]) == 1
    assert diag["near_miss"] is True
    assert "FVG" in diag["buy_blockers"][0]


def test_require_fvg_false_removes_fvg_blocker():
    diag = _diagnose(
        htf_bias="BULLISH",
        poi_tap="POI TAPPED",
        ltf_mss="BULLISH MSS",
        micro_fvg="NO FVG",
        delta=100,
        require_fvg=False,
    )
    assert diag["buy_blockers"] == []
    assert diag["total_conditions"] == 4


def test_closest_direction_prefers_fewer_blockers():
    diag = _diagnose(
        htf_bias="BULLISH",
        poi_tap="POI TAPPED",
        ltf_mss="BULLISH MSS",
        micro_fvg="NO FVG",  # only FVG missing
        delta=100,
    )
    assert diag["closest_direction"] == "BUY"
