"""
Per-step funnel instrumentation in the scanner.

For each non-firing evaluation, the FIRST blocker in canonical pipeline
order should increment `funnel_step_failures_total{pair, step, direction}`
and emit a `funnel_step_failed` structured log line. Test naming follows
the existing `test_scanner_emits_metrics.py` style.

Distinct from `test_funnel.py` (which is B3 signal-funnel widening).
"""

from unittest.mock import MagicMock

import pytest

from ictbot.orchestrator import scanner as scanner_mod


@pytest.fixture(autouse=True)
def _stub_metrics(monkeypatch):
    """Replace each metric with a MagicMock so we can assert calls."""
    sig = MagicMock()
    evals = MagicMock()
    lat = MagicMock()
    funnel = MagicMock()
    sig.labels.return_value = sig
    evals.labels.return_value = evals
    funnel.labels.return_value = funnel
    lat.time.return_value.__enter__ = MagicMock(return_value=None)
    lat.time.return_value.__exit__ = MagicMock(return_value=None)
    monkeypatch.setattr(scanner_mod.metrics, "signals_fired_total", sig)
    monkeypatch.setattr(scanner_mod.metrics, "evaluations_total", evals)
    monkeypatch.setattr(scanner_mod.metrics, "evaluate_latency_seconds", lat)
    monkeypatch.setattr(scanner_mod.metrics, "funnel_step_failures_total", funnel)
    return {"sig": sig, "evals": evals, "lat": lat, "funnel": funnel}


# ---- _blocker_to_step --------------------------------------------------------


def test_blocker_to_step_htf_bias():
    assert scanner_mod._blocker_to_step("HTF bias is WAITING (need BULLISH)") == "htf_bias"


def test_blocker_to_step_poi_tap():
    assert scanner_mod._blocker_to_step("POI not tapped") == "poi_tap"


def test_blocker_to_step_mss():
    assert scanner_mod._blocker_to_step("MSS is 'NO MSS' (need BULLISH MSS)") == "mss"


def test_blocker_to_step_fvg():
    assert scanner_mod._blocker_to_step("FVG is 'NO FVG' (need BULLISH FVG)") == "fvg"


def test_blocker_to_step_mfvg_retest():
    # The MFVG-retest message must NOT be misclassified as the FVG
    # bucket — it's a distinct downstream step.
    assert (
        scanner_mod._blocker_to_step("MFVG not retested (need a later close inside the gap)")
        == "mfvg_retest"
    )


def test_blocker_to_step_delta_sign_mode():
    assert scanner_mod._blocker_to_step("Delta is -5.2 (need > 0)") == "delta"


def test_blocker_to_step_delta_relative_mode():
    assert scanner_mod._blocker_to_step("Relative delta is 0.1 (need > 0.5)") == "delta"


def test_blocker_to_step_returns_none_for_unknown():
    assert scanner_mod._blocker_to_step("some new gate we haven't mapped") is None


def test_blocker_to_step_handles_empty_string():
    assert scanner_mod._blocker_to_step("") is None


# ---- _first_funnel_step ------------------------------------------------------


def test_first_funnel_step_returns_none_when_signal_fired():
    r = {
        "entry": "BUY",
        "diagnostics": {"blockers": ["HTF bias is WAITING (need BULLISH)"]},
    }
    assert scanner_mod._first_funnel_step(r) is None


def test_first_funnel_step_returns_gate_when_environmental_block():
    # gate_blocked short-circuits the eval before any ICT blocker matters.
    r = {
        "entry": "NO ENTRY",
        "gate_blocked": "outside killzone (London/NY closed)",
        "diagnostics": {"blockers": ["FVG is 'NO FVG' (need BULLISH FVG)"]},
    }
    assert scanner_mod._first_funnel_step(r) == "gate"


def test_first_funnel_step_returns_earliest_canonical_step():
    # With multiple blockers, the most upstream one in _STEP_ORDER wins.
    # Here: htf_bias precedes both mss and fvg → htf_bias.
    r = {
        "entry": "NO ENTRY",
        "diagnostics": {
            "blockers": [
                "FVG is 'NO FVG' (need BULLISH FVG)",
                "MSS is 'NO MSS' (need BULLISH MSS)",
                "HTF bias is WAITING (need BULLISH)",
            ],
        },
    }
    assert scanner_mod._first_funnel_step(r) == "htf_bias"


def test_first_funnel_step_returns_none_when_no_blockers_recognised():
    r = {
        "entry": "NO ENTRY",
        "diagnostics": {"blockers": ["some unmapped reason"]},
    }
    assert scanner_mod._first_funnel_step(r) is None


def test_first_funnel_step_handles_missing_diagnostics():
    r = {"entry": "NO ENTRY"}
    assert scanner_mod._first_funnel_step(r) is None


# ---- _emit_funnel via _evaluate_with_metrics --------------------------------


def test_no_entry_emits_funnel_counter(monkeypatch, _stub_metrics):
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {
            "error": None,
            "entry": "NO ENTRY",
            "pair": pair,
            "confidence": 50,
            "diagnostics": {
                "near_miss": False,
                "blockers": ["MSS is 'NO MSS' (need BULLISH MSS)"],
                "closest_direction": "BUY",
            },
        },
    )
    scanner_mod._evaluate_with_metrics("BTC/USDT:USDT")
    _stub_metrics["funnel"].labels.assert_called_once_with(
        pair="BTC/USDT:USDT", step="mss", direction="BUY"
    )
    _stub_metrics["funnel"].inc.assert_called_once()


def test_buy_signal_does_not_emit_funnel(monkeypatch, _stub_metrics):
    # A real fire means all gates passed — the funnel counter must stay
    # quiet so the dashboard's drop-off rates aren't polluted.
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {
            "error": None,
            "entry": "BUY",
            "pair": pair,
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
        },
    )
    scanner_mod._evaluate_with_metrics("BTC/USDT:USDT")
    _stub_metrics["funnel"].labels.assert_not_called()


def test_evaluation_error_does_not_emit_funnel(monkeypatch, _stub_metrics):
    # Errors already have their own outcome bucket; charging the funnel
    # for them would double-count and skew drop-off rates.
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {"error": "fetch failed", "entry": "NO ENTRY"},
    )
    scanner_mod._evaluate_with_metrics("BTC/USDT:USDT")
    _stub_metrics["funnel"].labels.assert_not_called()


def test_gate_blocked_emits_gate_step(monkeypatch, _stub_metrics):
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {
            "error": None,
            "entry": "NO ENTRY",
            "pair": pair,
            "gate_blocked": "regime is LOW_VOL",
            "confidence": 0,
            "diagnostics": {
                "near_miss": False,
                "blockers": ["HTF bias is WAITING (need BULLISH)"],
                "closest_direction": "BUY",
            },
        },
    )
    scanner_mod._evaluate_with_metrics("ETH/USDT:USDT")
    _stub_metrics["funnel"].labels.assert_called_once_with(
        pair="ETH/USDT:USDT", step="gate", direction="BUY"
    )


def test_unmapped_blocker_does_not_emit(monkeypatch, _stub_metrics):
    # If no blocker matches _STEP_ORDER, we'd rather miss a count than
    # mis-attribute it — silent skip is the safer behaviour.
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {
            "error": None,
            "entry": "NO ENTRY",
            "pair": pair,
            "diagnostics": {
                "near_miss": False,
                "blockers": ["totally novel reason"],
                "closest_direction": "BUY",
            },
        },
    )
    scanner_mod._evaluate_with_metrics("BTC/USDT:USDT")
    _stub_metrics["funnel"].labels.assert_not_called()
