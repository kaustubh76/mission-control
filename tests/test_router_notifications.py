"""
Tier 2 / Phase 5 — TG visibility tests.

Covers Fix 5.C (TG notify on close), 5.E (throttled rejection summary),
and Fix 5.D (emergency-flatten alert in BinanceLiveBroker).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ictbot.exec.binance_live import BinanceLiveBroker
from ictbot.exec.orders import Order
from ictbot.orchestrator.router import SignalRouter
from ictbot.portfolio.caps import CapGate, MaxOpenPositions
from ictbot.settings import settings

# ---- Fix 5.C: TG notify on close ------------------------------------------


def _filled_order(
    side="BUY", entry=100.0, sl=95.0, tp=110.0, close_price=110.0, qty=0.5, reason="TP", fees=0.05
):
    o = Order(pair="BTC/USDT:USDT", side=side, entry=entry, sl=sl, tp=tp, qty=qty)
    o.status = "FILLED"
    o.close_price = close_price
    o.close_reason = reason
    o.closed_at = datetime(2026, 6, 6, tzinfo=timezone.utc)
    o.fees_paid = fees
    return o


def _live_router(broker_name: str = "binance-live"):
    broker = MagicMock()
    broker.name = broker_name
    broker._on_close = None
    broker.positions = MagicMock(return_value=[])
    return SignalRouter(
        broker=broker, cap_gate=CapGate([MaxOpenPositions(1)]), is_live=True, journal=MagicMock()
    )


def test_on_close_sends_tg_when_enabled(monkeypatch):
    """Fix 5.C: live router fires TG on close when TG_NOTIFY_ON_CLOSE=True."""
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_ON_CLOSE", True)
    router = _live_router()
    with patch("ictbot.notify.telegram.send_telegram") as send:
        router.on_close(_filled_order())
        assert send.called
        msg = send.call_args[0][0]
        assert "CLOSE BTC/USDT:USDT BUY" in msg
        assert "reason=TP" in msg
        # Net R after fees: (110-100)/5 - 0.05/(0.5*5) = 2.0 - 0.02 = 1.98
        assert "R=+1.980" in msg
        assert "fees=$0.0500" in msg


def test_on_close_skips_tg_when_disabled(monkeypatch):
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_ON_CLOSE", False)
    router = _live_router()
    with patch("ictbot.notify.telegram.send_telegram") as send:
        router.on_close(_filled_order())
        assert not send.called


def test_on_close_skips_tg_when_paper_router(monkeypatch):
    """Paper-only routers (is_live=False) must NOT spam TG with paper
    close events — those are for in-process analysis, not operator
    attention."""
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_ON_CLOSE", True)
    broker = MagicMock()
    broker.name = "paper"
    broker._on_close = None
    broker.positions = MagicMock(return_value=[])
    router = SignalRouter(
        broker=broker, cap_gate=CapGate([MaxOpenPositions(1)]), is_live=False, journal=MagicMock()
    )
    with patch("ictbot.notify.telegram.send_telegram") as send:
        router.on_close(_filled_order())
        assert not send.called


def test_on_close_tg_failure_does_not_break_close_handling(monkeypatch):
    """A flaky TG send must NOT prevent the journal mirror + cap +
    account updates from happening. Robustness over perfection."""
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_ON_CLOSE", True)
    router = _live_router()
    router.account = MagicMock()
    with patch("ictbot.notify.telegram.send_telegram", side_effect=RuntimeError("TG down")):
        # Must not raise.
        router.on_close(_filled_order())
        # Account.book_close still happened.
        assert router.account.book_close.called


def test_on_close_tags_reconciled_stub_in_message(monkeypatch):
    """Order.is_reconciled=True (rebuilt from fetch_positions on
    restart) gets a [reconciled stub] prefix so the operator knows
    the R is approximate."""
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_ON_CLOSE", True)
    router = _live_router()
    o = _filled_order()
    o.is_reconciled = True
    with patch("ictbot.notify.telegram.send_telegram") as send:
        router.on_close(o)
        assert send.called
        assert send.call_args[0][0].startswith("[reconciled stub] ")


# ---- Fix 5.E: throttled rejection summary ---------------------------------


def _result(pair="BTC/USDT:USDT"):
    return {
        "pair": pair,
        "entry": "BUY",
        "price": 100.0,
        "sl": 99.0,
        "tp": 103.0,
        "rr": 3.0,
        "confidence": 100,
    }


def test_journal_rejected_silent_when_threshold_zero(monkeypatch):
    """Default TG_NOTIFY_REJECTIONS_EVERY=0 means no TG noise at all."""
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_REJECTIONS_EVERY", 0)
    router = _live_router()
    with patch("ictbot.notify.telegram.send_telegram") as send:
        for _ in range(20):
            router._journal_rejected(_result(), "max_open_positions (1) reached")
        assert not send.called


def test_journal_rejected_fires_every_nth_rejection(monkeypatch):
    """At threshold=3, TG fires on the 3rd, 6th, 9th rejection of the
    same (pair, reason). 1st, 2nd, 4th, 5th must stay silent."""
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_REJECTIONS_EVERY", 3)
    router = _live_router()
    with patch("ictbot.notify.telegram.send_telegram") as send:
        for i in range(1, 8):
            router._journal_rejected(_result(), "max_open_positions (1) reached")
        # Fires on 3rd and 6th.
        assert send.call_count == 2


def test_journal_rejected_dedup_per_pair_reason(monkeypatch):
    """Different pairs share a counter only within their own (pair,
    reason) tuple — never crosswise."""
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_REJECTIONS_EVERY", 2)
    router = _live_router()
    with patch("ictbot.notify.telegram.send_telegram") as send:
        # 1st BTC rejection — silent.
        router._journal_rejected(_result("BTC/USDT:USDT"), "max_open_positions (1) reached")
        # 1st ETH rejection — silent (different pair counter).
        router._journal_rejected(_result("ETH/USDT:USDT"), "max_open_positions (1) reached")
        # 2nd BTC rejection — fires.
        router._journal_rejected(_result("BTC/USDT:USDT"), "max_open_positions (1) reached")
        assert send.call_count == 1
        assert "BTC/USDT:USDT" in send.call_args[0][0]


def test_journal_rejected_silent_when_paper(monkeypatch):
    """Paper routers don't spam TG with rejections regardless of
    threshold — the visibility is only for live."""
    monkeypatch.setattr("ictbot.settings.TG_NOTIFY_REJECTIONS_EVERY", 1)
    broker = MagicMock()
    broker.name = "paper"
    broker._on_close = None
    broker.positions = MagicMock(return_value=[])
    router = SignalRouter(
        broker=broker, cap_gate=CapGate([MaxOpenPositions(1)]), is_live=False, journal=MagicMock()
    )
    with patch("ictbot.notify.telegram.send_telegram") as send:
        router._journal_rejected(_result(), "max_open_positions (1) reached")
        assert not send.called


# ---- Fix 5.D: emergency-flatten TG alert ---------------------------------


@pytest.fixture(autouse=True)
def _enable_live(monkeypatch):
    monkeypatch.setattr(settings, "enable_live_trading", True)


def test_emergency_flatten_sends_tg_on_failure():
    """When the reduce-only flatten itself raises, the operator must
    receive a TG critical alert tagged [BOT EMERGENCY]."""
    client = MagicMock()
    # First call (entry) succeeds; second call (SL placement) raises;
    # third call (emergency flatten) ALSO raises — that's the bad path.
    client.create_order.side_effect = [
        {"id": "entry-1"},  # entry
        Exception("SL placement failed"),
        Exception("emergency flatten also failed"),
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.0, sl=95.0, tp=110.0, qty=0.5)
    with patch("ictbot.notify.telegram.send_telegram") as send:
        with pytest.raises(Exception, match="SL placement failed"):
            b.place_order(o)
        # Emergency flatten failed → TG alert was sent.
        assert send.called
        msg = send.call_args[0][0]
        assert "[BOT EMERGENCY]" in msg
        assert "BTC/USDT:USDT" in msg
        assert "manual intervention" in msg.lower()


def test_emergency_flatten_no_tg_when_flatten_succeeds():
    """The TG alert ONLY fires when the flatten itself fails. A clean
    flatten after SL/TP failure is the expected recovery path and
    should not generate a critical alert."""
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1"},
        Exception("SL placement failed"),
        {"id": "flatten-1"},  # successful flatten
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.0, sl=95.0, tp=110.0, qty=0.5)
    with patch("ictbot.notify.telegram.send_telegram") as send:
        with pytest.raises(Exception, match="SL placement failed"):
            b.place_order(o)
        assert not send.called


def test_emergency_flatten_tg_send_failure_does_not_mask_critical():
    """If TG itself is down when emergency-flatten fails, the
    critical log line must still go out and the original exception
    must still propagate. Notification failure is never allowed to
    silently absorb a safety-critical event."""
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1"},
        Exception("SL placement failed"),
        Exception("emergency flatten also failed"),
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.0, sl=95.0, tp=110.0, qty=0.5)
    with patch("ictbot.notify.telegram.send_telegram", side_effect=RuntimeError("TG also down")):
        # The original SL-placement-failed exception MUST still propagate.
        with pytest.raises(Exception, match="SL placement failed"):
            b.place_order(o)
