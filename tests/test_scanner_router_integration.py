"""
C2 follow-up — scanner wires the SignalRouter into BUY/SELL signals.

Verifies:
  - _build_router picks PaperBroker when live trading is off.
  - _build_router picks the configured live broker when live trading is
    on AND the kill switch is not engaged.
  - _route_signal calls the router and journals the placement.
  - _route_signal does not raise when the broker / router blows up.
"""

from unittest.mock import MagicMock

from ictbot.exec.factory import LiveTradingDisabled
from ictbot.exec.paper import PaperBroker
from ictbot.orchestrator import scanner as scanner_mod
from ictbot.orchestrator.router import RouteOutcome, SignalRouter
from ictbot.settings import settings


def _signal(entry="BUY"):
    # confidence=100 matches the default AUTO_EXECUTE_MIN_CONFIDENCE so
    # the Phase D tier branch routes straight through to the broker.
    return {
        "pair": "BTC/USDT:USDT",
        "entry": entry,
        "price": 100.0,
        "sl": 99.0,
        "tp": 103.0,
        "rr": 3.0,
        "confidence": 100,
        "error": None,
        "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
    }


# ---- _build_router selection ------------------------------------------------


def test_build_router_uses_paper_broker_when_live_off(monkeypatch):
    monkeypatch.setattr(settings, "enable_live_trading", False)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)
    router = scanner_mod._build_router()
    assert isinstance(router.broker, PaperBroker)


def test_build_router_uses_paper_broker_when_kill_switch_engaged(monkeypatch):
    """Even with live trading enabled in env, an engaged kill switch
    must keep us on paper. Fail-safe direction."""
    monkeypatch.setattr(settings, "enable_live_trading", True)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: True)
    router = scanner_mod._build_router()
    assert isinstance(router.broker, PaperBroker)


def test_build_router_uses_live_broker_when_both_gates_open(monkeypatch):
    """Venue-agnostic: the factory routes to whichever venue settings.exchange
    names. We only assert it's NOT a paper broker."""
    monkeypatch.setattr(settings, "enable_live_trading", True)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)
    router = scanner_mod._build_router()
    assert not isinstance(router.broker, PaperBroker)
    assert router.broker.name != "paper"


def test_build_router_installs_default_caps(monkeypatch):
    monkeypatch.setattr(settings, "enable_live_trading", False)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)
    router = scanner_mod._build_router()
    assert len(router.cap_gate.caps) >= 2  # MaxOpenPositions + DailyLossLimit


# ---- Fix 2.I: on_reconnect wiring -------------------------------------------


def test_build_router_calls_on_reconnect_when_live(monkeypatch):
    """Fix 2.I (plan: live P&L clean-up follow-up): when the live broker
    exposes on_reconnect, _build_router must call it so pre-existing
    positions on the exchange are reflected in broker._orders. Without
    this, a restart with an open position causes the MaxOpenPositions
    cap to silently bypass on the next signal (the 2026-06-05 PAXG
    orphan-doubling regression)."""
    monkeypatch.setattr(settings, "enable_live_trading", True)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)
    reconnect_calls = []

    class _FakeLiveBroker:
        name = "fake-live"
        _on_close = None

        def on_reconnect(self):
            reconnect_calls.append(1)

        def positions(self):
            return []

        def qty_step(self, pair):
            return 0.001

        def min_notional(self, pair):
            return 0.0

        def equity(self):
            return 10_000.0

    monkeypatch.setattr(scanner_mod, "build_live_broker", lambda **kw: _FakeLiveBroker())
    scanner_mod._build_router()
    assert reconnect_calls == [1]


def test_build_router_skips_on_reconnect_when_paper(monkeypatch):
    """PaperBroker doesn't expose on_reconnect (no exchange state to
    repopulate from). The wiring must only fire on the live path."""
    monkeypatch.setattr(settings, "enable_live_trading", False)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)
    router = scanner_mod._build_router()
    # Sanity: PaperBroker has no on_reconnect attribute. If a future
    # paper broker grows one, the live-gate (`if live and ...`) means
    # it still won't be invoked here.
    assert not hasattr(router.broker, "on_reconnect")


def test_build_router_swallows_on_reconnect_failure(monkeypatch):
    """A transient ccxt hiccup during reconcile must NOT block scanner
    startup. Better partial state than no scanner at all."""
    monkeypatch.setattr(settings, "enable_live_trading", True)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)

    class _FlakyLiveBroker:
        name = "flaky-live"
        _on_close = None

        def on_reconnect(self):
            raise RuntimeError("ccxt network blip during reconcile")

        def positions(self):
            return []

        def qty_step(self, pair):
            return 0.001

        def min_notional(self, pair):
            return 0.0

        def equity(self):
            return 10_000.0

    monkeypatch.setattr(scanner_mod, "build_live_broker", lambda **kw: _FlakyLiveBroker())
    # Must not raise; router still constructs.
    router = scanner_mod._build_router()
    assert router is not None
    assert router.broker.name == "flaky-live"


# ---- Fix 2.D: RISK_PCT_LIVE wiring -----------------------------------------


def test_live_router_uses_risk_pct_live_when_shadow_off(monkeypatch):
    """Fix 2.D regression cover: the live router must use RISK_PCT_LIVE
    even when SHADOW_MODE is off. Pre-fix it silently fell back to
    RISK_PCT (10x larger), allowing the bot to size positions at 0.5 %
    while the boot guard only protected RISK_PCT_LIVE."""
    monkeypatch.setattr(settings, "enable_live_trading", True)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)
    monkeypatch.setattr(scanner_mod, "RISK_PCT_LIVE", 0.0005)
    monkeypatch.setattr(scanner_mod, "RISK_PCT", 0.005)
    monkeypatch.setattr(scanner_mod, "SHADOW_MODE", False)
    router = scanner_mod._build_router()
    assert router.risk_pct == 0.0005


def test_live_router_uses_risk_pct_live_when_shadow_on(monkeypatch):
    """The shadow leg still keeps RISK_PCT for the apples-to-apples
    comparison; only the *live* leg uses RISK_PCT_LIVE."""
    monkeypatch.setattr(settings, "enable_live_trading", True)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)
    monkeypatch.setattr(scanner_mod, "RISK_PCT_LIVE", 0.0005)
    monkeypatch.setattr(scanner_mod, "RISK_PCT", 0.005)
    monkeypatch.setattr(scanner_mod, "SHADOW_MODE", True)
    wrapper = scanner_mod._build_router()
    # ShadowRouter wraps both legs; the live leg uses RISK_PCT_LIVE,
    # the shadow leg uses RISK_PCT.
    assert wrapper.live_router.risk_pct == 0.0005
    assert wrapper.shadow_router.risk_pct == 0.005


def test_paper_router_uses_risk_pct_not_risk_pct_live(monkeypatch):
    """When live trading is OFF, the paper router uses the wider
    RISK_PCT so backtests / staging stay at their usual sizing."""
    monkeypatch.setattr(settings, "enable_live_trading", False)
    monkeypatch.setattr(scanner_mod.kill_switch, "is_engaged", lambda: False)
    monkeypatch.setattr(scanner_mod, "RISK_PCT_LIVE", 0.0005)
    monkeypatch.setattr(scanner_mod, "RISK_PCT", 0.005)
    monkeypatch.setattr(scanner_mod, "SHADOW_MODE", False)
    router = scanner_mod._build_router()
    assert router.risk_pct == 0.005


# ---- _route_signal logging + journal ---------------------------------------


def test_route_signal_calls_router_route(monkeypatch):
    router = MagicMock(spec=SignalRouter)
    placed_order = MagicMock(side="BUY", pair="BTC/USDT:USDT", qty=1.0, entry=100, sl=99, tp=103)
    router.route.return_value = RouteOutcome(placed=True, order=placed_order, rejection=None)
    scanner_mod._route_signal(router, _signal())
    router.route.assert_called_once()


def test_route_signal_swallows_live_trading_disabled(monkeypatch):
    router = MagicMock(spec=SignalRouter)
    router.route.side_effect = LiveTradingDisabled("test gate")
    # Should NOT raise — the scanner keeps running.
    scanner_mod._route_signal(router, _signal())


def test_route_signal_swallows_unexpected_errors(monkeypatch):
    router = MagicMock(spec=SignalRouter)
    router.route.side_effect = RuntimeError("oops")
    scanner_mod._route_signal(router, _signal())  # no raise


def test_route_signal_increments_cap_rejection_metric(monkeypatch):
    from ictbot.portfolio.caps import CapDecision

    metric = MagicMock()
    metric.labels.return_value = metric
    monkeypatch.setattr(scanner_mod.metrics, "cap_rejections_total", metric)

    router = MagicMock(spec=SignalRouter)
    router.route.return_value = RouteOutcome(
        placed=False,
        order=None,
        rejection=CapDecision(False, "max_open_positions (1) reached"),
    )
    scanner_mod._route_signal(router, _signal())
    # Label uses the first whitespace-delimited word of the reason.
    metric.labels.assert_called_once_with(cap="max_open_positions")
    metric.inc.assert_called_once()


# ---- scanner module surface -------------------------------------------------


def test_scanner_exposes_router_builder():
    """Smoke check: the integration symbols are importable and callable."""
    assert callable(scanner_mod._build_router)
    assert callable(scanner_mod._route_signal)
