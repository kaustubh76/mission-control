"""
G1 (ROADMAP §G1) — typed Signal dataclass acceptance tests.

Round-trip parity with the legacy dict: from_dict(d).to_dict() must equal
d on the fields Signal claims to own.
"""

import pytest

from ictbot.strategy.signal import Signal


def _legacy_dict():
    return {
        "pair": "BTC/USDT:USDT",
        "error": None,
        "price": 100.0,
        "last_close": 100.0,
        "htf_bias": "BULLISH",
        "ltf_bias": "BULLISH",
        "ltf_poi": 95.0,
        "poi_tap": "POI TAPPED",
        "ltf_mss": "BULLISH MSS",
        "fvg": "BULLISH FVG",
        "micro_fvg": "BULLISH FVG",
        "delta": 12.5,
        "relative_delta": 0.7,
        "delta_mode": "relative",
        "atr_1m": 1.5,
        "entry": "BUY",
        "sl": 98.0,
        "tp": 106.0,
        "rr": 3.0,
        "confidence": 75,
        "gate_blocked": None,
        "regime": "HIGH_VOL",
        "diagnostics": {
            "buy_blockers": [],
            "sell_blockers": ["HTF bias is BULLISH"],
            "closest_direction": "BUY",
            "blockers": [],
            "near_miss": False,
            "total_conditions": 5,
        },
    }


def test_round_trip_preserves_all_fields():
    d = _legacy_dict()
    s = Signal.from_dict(d)
    assert s.to_dict() == d


def test_from_dict_drops_unknown_keys():
    d = _legacy_dict()
    d["surplus_key"] = 12345
    d["another"] = "ignored"
    s = Signal.from_dict(d)
    assert "surplus_key" not in s.to_dict()
    # Known keys still round-trip.
    assert s.to_dict()["entry"] == "BUY"


def test_from_dict_fills_defaults_when_keys_missing():
    s = Signal.from_dict({"pair": "ETH/USDT:USDT"})
    assert s.pair == "ETH/USDT:USDT"
    assert s.entry == "NO ENTRY"
    assert s.confidence == 0
    assert s.diagnostics == {}


def test_is_actionable_true_for_buy_with_no_error():
    s = Signal.from_dict(_legacy_dict())
    assert s.is_actionable() is True


def test_is_actionable_false_for_no_entry():
    d = _legacy_dict()
    d["entry"] = "NO ENTRY"
    s = Signal.from_dict(d)
    assert s.is_actionable() is False


def test_is_actionable_false_when_error_present():
    d = _legacy_dict()
    d["error"] = "fetch failed"
    s = Signal.from_dict(d)
    assert s.is_actionable() is False


def test_risk_distance_returns_abs_entry_to_sl():
    s = Signal.from_dict(_legacy_dict())
    assert s.risk_distance() == pytest.approx(2.0)


def test_risk_distance_zero_when_not_actionable():
    d = _legacy_dict()
    d["entry"] = "NO ENTRY"
    s = Signal.from_dict(d)
    assert s.risk_distance() == 0.0


def test_signal_is_frozen():
    s = Signal.from_dict(_legacy_dict())
    # frozen dataclass raises FrozenInstanceError (a dataclasses-internal
    # subclass of AttributeError) when mutation is attempted.
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        s.entry = "SELL"  # type: ignore[misc]
