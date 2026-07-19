"""
Phase D — tiered autonomy tests.

Covers the 3-tier decision in scanner._route_signal:
  - conf >= AUTO_EXECUTE_MIN_CONFIDENCE → AUTO (router.route called)
  - conf <  threshold AND TG_CONFIRM_MODE=true → CONFIRM (TG send called)
  - conf <  threshold AND TG_CONFIRM_MODE=false → DROP (neither called)

Plus the live_trades_total counter on successful placements and the
boot-time risk guard refusal.
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from ictbot.exec.paper import PaperBroker
from ictbot.orchestrator import scanner as scanner_mod
from ictbot.orchestrator.router import SignalRouter
from ictbot.runtime import metrics


def _signal(conf: int = 100, entry: str = "BUY"):
    return {
        "pair": "BTC/USDT:USDT",
        "entry": entry,
        "price": 100.0,
        "sl": 99.0,
        "tp": 103.0,
        "rr": 3.0,
        "confidence": conf,
        "error": None,
    }


class _RouterSpy:
    def __init__(self):
        self.calls: list[dict] = []

    def route(self, result):
        self.calls.append(result)
        # placed=False so scanner._route_signal short-circuits without
        # touching outcome.order.* (we only assert route was called).
        return SimpleNamespace(placed=False, order=None, rejection=None)


class _TgSpy:
    def __init__(self):
        self.queued: list[dict] = []

    def send_signal_with_buttons(self, result):
        self.queued.append(result)
        return "sid-1"


# ---- tier branch ----------------------------------------------------------


def test_auto_tier_at_threshold(monkeypatch):
    monkeypatch.setattr(scanner_mod, "AUTO_EXECUTE_MIN_CONFIDENCE", 75)
    monkeypatch.setattr(scanner_mod, "TG_CONFIRM_MODE", False)
    monkeypatch.setattr(scanner_mod, "_tg_confirm", None)
    router = _RouterSpy()
    scanner_mod._route_signal(router, _signal(conf=75))
    assert len(router.calls) == 1


def test_auto_tier_above_threshold(monkeypatch):
    monkeypatch.setattr(scanner_mod, "AUTO_EXECUTE_MIN_CONFIDENCE", 75)
    monkeypatch.setattr(scanner_mod, "TG_CONFIRM_MODE", False)
    monkeypatch.setattr(scanner_mod, "_tg_confirm", None)
    router = _RouterSpy()
    scanner_mod._route_signal(router, _signal(conf=100))
    assert len(router.calls) == 1


def test_confirm_tier_below_threshold_with_tg_on(monkeypatch):
    monkeypatch.setattr(scanner_mod, "AUTO_EXECUTE_MIN_CONFIDENCE", 100)
    monkeypatch.setattr(scanner_mod, "TG_CONFIRM_MODE", True)
    tg = _TgSpy()
    monkeypatch.setattr(scanner_mod, "_tg_confirm", tg)
    router = _RouterSpy()
    scanner_mod._route_signal(router, _signal(conf=75))
    assert tg.queued == [_signal(conf=75)] or len(tg.queued) == 1
    assert router.calls == []  # auto path NOT taken


def test_drop_tier_below_threshold_no_tg(monkeypatch):
    monkeypatch.setattr(scanner_mod, "AUTO_EXECUTE_MIN_CONFIDENCE", 100)
    monkeypatch.setattr(scanner_mod, "TG_CONFIRM_MODE", False)
    monkeypatch.setattr(scanner_mod, "_tg_confirm", None)
    router = _RouterSpy()
    scanner_mod._route_signal(router, _signal(conf=75))
    assert router.calls == []


def test_confirm_tier_skipped_when_tg_service_missing(monkeypatch):
    """TG_CONFIRM_MODE=true but _tg_confirm is None (service failed to
    boot) → must DROP, not crash."""
    monkeypatch.setattr(scanner_mod, "AUTO_EXECUTE_MIN_CONFIDENCE", 100)
    monkeypatch.setattr(scanner_mod, "TG_CONFIRM_MODE", True)
    monkeypatch.setattr(scanner_mod, "_tg_confirm", None)
    router = _RouterSpy()
    scanner_mod._route_signal(router, _signal(conf=75))
    assert router.calls == []


# ---- live_trades_total metric --------------------------------------------
#
# We replace metrics.live_trades_total with a spy so the test is robust to
# whether prometheus_client is installed (no-op shim swallows inc() calls
# silently otherwise, which makes assertions impossible).


class _CounterSpy:
    def __init__(self):
        self.calls: list[tuple] = []

    def labels(self, **kw):
        outer = self

        class _Bound:
            def inc(self, *_a, **_kw):
                outer.calls.append(tuple(sorted(kw.items())))

        return _Bound()


def test_live_trades_metric_increments_on_live_placement(monkeypatch):
    spy = _CounterSpy()
    monkeypatch.setattr(metrics, "live_trades_total", spy)
    broker = PaperBroker()
    router = SignalRouter(broker=broker, balance=10_000, risk_pct=0.01, is_live=True)
    out = router.route(_signal(conf=100))
    assert out.placed is True
    assert spy.calls == [
        (("direction", "BUY"), ("pair", "BTC/USDT:USDT")),
    ]


def test_live_trades_metric_not_incremented_for_paper_router(monkeypatch):
    spy = _CounterSpy()
    monkeypatch.setattr(metrics, "live_trades_total", spy)
    broker = PaperBroker()
    router = SignalRouter(broker=broker, balance=10_000, risk_pct=0.01, is_live=False)
    out = router.route(_signal(conf=100, entry="SELL"))
    assert out.placed is True
    assert spy.calls == []


# ---- boot-time risk guard -------------------------------------------------


def test_boot_guard_refuses_risk_over_cap():
    """Importing settings with ENABLE_LIVE_TRADING=true and
    RISK_PCT_LIVE > MAX_LIVE_RISK_PER_TRADE_PCT must fail."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from ictbot.settings import settings; print('FAIL')",
        ],
        env={
            "PATH": "/usr/bin:/bin",
            "ENABLE_LIVE_TRADING": "true",
            "RISK_PCT_LIVE": "0.01",
            "MAX_LIVE_RISK_PER_TRADE_PCT": "0.001",
            # PYTHONPATH so the subprocess finds the package without
            # a full venv activation.
            "PYTHONPATH": "src",
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "MAX_LIVE_RISK_PER_TRADE_PCT" in (proc.stderr + proc.stdout)
