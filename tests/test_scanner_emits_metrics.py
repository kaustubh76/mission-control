"""
D1 (ROADMAP §D1) — scanner emits Prometheus metrics + JSON log entries.

The full scan loop is `while True: ... sleep(30)`; we can't run that in
a unit test. Instead exercise `_evaluate_with_metrics`, the wrapper
that records each iteration's outcome. That's where the metrics
catalogue from runtime.metrics actually gets touched.
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
    # `.labels(...)` returns the metric again (for `.inc()`/`.observe()`).
    sig.labels.return_value = sig
    evals.labels.return_value = evals
    # `with metrics.evaluate_latency_seconds.time():` uses ctx manager.
    lat.time.return_value.__enter__ = MagicMock(return_value=None)
    lat.time.return_value.__exit__ = MagicMock(return_value=None)
    monkeypatch.setattr(scanner_mod.metrics, "signals_fired_total", sig)
    monkeypatch.setattr(scanner_mod.metrics, "evaluations_total", evals)
    monkeypatch.setattr(scanner_mod.metrics, "evaluate_latency_seconds", lat)
    return {"sig": sig, "evals": evals, "lat": lat}


def test_buy_signal_increments_both_counters(monkeypatch, _stub_metrics):
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {
            "error": None,
            "entry": "BUY",
            "pair": pair,
            "price": 100.0,
            "sl": 99.0,
            "tp": 103.0,
            "rr": 3.0,
            "confidence": 75,
            "htf_bias": "BULLISH",
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
        },
    )
    r = scanner_mod._evaluate_with_metrics("BTC/USDT:USDT")
    assert r["entry"] == "BUY"
    _stub_metrics["sig"].labels.assert_called_once_with(pair="BTC/USDT:USDT", direction="BUY")
    _stub_metrics["sig"].inc.assert_called_once()
    _stub_metrics["evals"].labels.assert_called_once_with(pair="BTC/USDT:USDT", outcome="signal")


def test_sell_signal_increments_with_sell_direction(monkeypatch, _stub_metrics):
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {
            "error": None,
            "entry": "SELL",
            "pair": pair,
            "price": 100.0,
            "sl": 101.0,
            "tp": 97.0,
            "rr": 3.0,
            "confidence": 50,
            "htf_bias": "BEARISH",
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "SELL"},
        },
    )
    scanner_mod._evaluate_with_metrics("ETH/USDT:USDT")
    _stub_metrics["sig"].labels.assert_called_once_with(pair="ETH/USDT:USDT", direction="SELL")


def test_no_entry_does_not_increment_signal_counter(monkeypatch, _stub_metrics):
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {
            "error": None,
            "entry": "NO ENTRY",
            "pair": pair,
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
        },
    )
    scanner_mod._evaluate_with_metrics("BTC/USDT:USDT")
    _stub_metrics["sig"].labels.assert_not_called()
    _stub_metrics["evals"].labels.assert_called_once_with(pair="BTC/USDT:USDT", outcome="no_entry")


def test_evaluation_error_increments_error_outcome(monkeypatch, _stub_metrics):
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {"error": "fetch failed", "entry": "NO ENTRY"},
    )
    scanner_mod._evaluate_with_metrics("XRP/USDT:USDT")
    _stub_metrics["evals"].labels.assert_called_once_with(pair="XRP/USDT:USDT", outcome="error")
    _stub_metrics["sig"].labels.assert_not_called()


def test_latency_histogram_is_observed_around_each_call(monkeypatch, _stub_metrics):
    """`with evaluate_latency_seconds.time():` should enter/exit."""
    monkeypatch.setattr(
        scanner_mod,
        "analyze_pair",
        lambda pair, notify=True: {
            "error": None,
            "entry": "NO ENTRY",
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
        },
    )
    scanner_mod._evaluate_with_metrics("BTC/USDT:USDT")
    # .time() returned a ctx manager that __enter__ed + __exit__ed once.
    _stub_metrics["lat"].time.assert_called_once()


def test_scanner_module_has_json_logger():
    # jlog must be the JSON logger so dashboards can ingest structured fields.
    assert scanner_mod.jlog.name == "scanner"
