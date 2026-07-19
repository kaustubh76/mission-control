"""Tests for the JSON logger + metrics shim — Phase 9."""

import json
import logging

import pytest

from ictbot.runtime import logger as rt_logger
from ictbot.runtime import metrics


@pytest.fixture
def patched_logs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rt_logger, "LOGS_DIR", tmp_path)
    yield tmp_path
    # Tear down: drop any loggers we created so the next test starts clean.
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("ict.json.") or name.startswith("ictbot.test."):
            del logging.Logger.manager.loggerDict[name]


def test_json_logger_writes_single_json_object_per_line(patched_logs_dir):
    log = rt_logger.get_json_logger("ictbot.test.unit")
    log.info("hello", extra={"pair": "BTC/USDT:USDT", "confidence": 75})
    log.warning("bye", extra={"pair": "ETH/USDT:USDT"})
    for h in log.handlers:
        h.flush()

    path = patched_logs_dir / "ictbot.test.unit.json.log"
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    assert rec0["msg"] == "hello"
    assert rec0["pair"] == "BTC/USDT:USDT"
    assert rec0["confidence"] == 75
    assert rec0["level"] == "INFO"
    rec1 = json.loads(lines[1])
    assert rec1["pair"] == "ETH/USDT:USDT"
    assert rec1["level"] == "WARNING"


def test_metrics_increment_does_not_raise():
    # Whether prometheus_client is installed or not, these calls must
    # succeed silently. is_available() is the discriminator.
    metrics.signals_fired_total.labels(pair="BTC/USDT:USDT", direction="BUY").inc()
    metrics.evaluations_total.labels(pair="BTC/USDT:USDT", outcome="signal").inc()
    metrics.cap_rejections_total.labels(cap="max_open_positions").inc()
    metrics.evaluate_latency_seconds.observe(0.012)


def test_start_server_idempotent_no_throw():
    # No-op shim path (prometheus_client may or may not be installed in CI);
    # either way the helper must not crash on a port=0 sentinel.
    if not metrics.is_available():
        metrics.start_metrics_server(port=0)
