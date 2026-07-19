"""
C2 (ROADMAP §C2) — SignalRouter tests.

Verifies the glue between Strategy → CapGate → Broker:
  - NO ENTRY signals never reach the broker
  - BUY/SELL signals consult the CapGate's current open positions
  - cap rejection blocks placement
  - successful placement produces a sized Order on the broker
  - sizing scales risk by balance × risk_pct ÷ distance(entry, sl)
  - journal + notifier callbacks fire (when supplied) but failure inside
    them does NOT block placement
"""

from __future__ import annotations

from ictbot.exec.orders import Order
from ictbot.exec.paper import PaperBroker
from ictbot.orchestrator.router import SignalRouter, _qty_for_risk
from ictbot.portfolio.caps import CapGate, MaxOpenPositions


def _signal(entry="BUY", price=100.0, sl=99.0, tp=103.0, rr=3.0, pair="BTC/USDT:USDT"):
    return {
        "pair": pair,
        "entry": entry,
        "price": price,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "confidence": 75,
        "error": None,
    }


# ---- sizing math ------------------------------------------------------------


def test_qty_for_risk_scales_with_distance():
    # $10k * 1% = $100 risk; distance = 1 → qty = 100.
    q = _qty_for_risk(balance=10_000, risk_pct=0.01, entry=100, sl=99)
    assert q == 100.0


def test_qty_for_risk_doubles_when_distance_halves():
    big = _qty_for_risk(balance=10_000, risk_pct=0.01, entry=100, sl=99.5)  # dist 0.5
    small = _qty_for_risk(balance=10_000, risk_pct=0.01, entry=100, sl=99)  # dist 1.0
    assert big == 2 * small


def test_qty_for_risk_zero_when_sl_equals_entry():
    assert _qty_for_risk(balance=10_000, risk_pct=0.01, entry=100, sl=100) == 0.0


# ---- routing branches -------------------------------------------------------


def test_no_entry_signal_does_not_call_broker():
    broker = PaperBroker()
    router = SignalRouter(broker=broker)
    out = router.route(_signal(entry="NO ENTRY"))
    assert out.placed is False
    assert broker.positions() == []


def test_buy_signal_places_order_and_returns_outcome():
    broker = PaperBroker()
    router = SignalRouter(broker=broker, balance=10_000, risk_pct=0.01)
    out = router.route(_signal(entry="BUY"))
    assert out.placed is True
    assert isinstance(out.order, Order)
    assert out.order.side == "BUY"
    assert out.order.entry == 100.0
    assert len(broker.positions()) == 1


def test_sell_signal_places_order():
    broker = PaperBroker()
    router = SignalRouter(broker=broker, balance=10_000, risk_pct=0.01)
    out = router.route(_signal(entry="SELL", price=100, sl=101, tp=97))
    assert out.placed is True
    assert out.order.side == "SELL"


def test_cap_rejection_blocks_placement():
    broker = PaperBroker()
    # Pre-fill broker with one open order so MaxOpenPositions(1) rejects.
    broker.place_order(Order(pair="BTC/USDT:USDT", side="BUY", entry=100, sl=99, tp=103, qty=1))
    router = SignalRouter(broker=broker, cap_gate=CapGate([MaxOpenPositions(1)]))
    out = router.route(_signal())
    assert out.placed is False
    assert out.rejection is not None
    assert "max_open_positions" in out.rejection.reason
    assert len(broker.positions()) == 1  # still just the pre-existing one


def test_cap_decision_allows_placement_when_below_cap():
    broker = PaperBroker()
    router = SignalRouter(broker=broker, cap_gate=CapGate([MaxOpenPositions(3)]))
    assert router.route(_signal()).placed is True
    assert router.route(_signal()).placed is True
    out = router.route(_signal())
    assert out.placed is True
    assert len(broker.positions()) == 3


def test_cap_gate_consults_live_broker_positions(monkeypatch):
    """Caps must read positions from the broker each call — not cache them
    across calls. A trade closing externally has to free a slot."""
    broker = PaperBroker()
    router = SignalRouter(broker=broker, cap_gate=CapGate([MaxOpenPositions(1)]))

    o1 = broker.place_order(
        Order(pair="BTC/USDT:USDT", side="BUY", entry=100, sl=99, tp=103, qty=1)
    )
    # Cap is full.
    assert router.route(_signal()).placed is False

    # Close the existing position (simulate TP hit).
    broker._close(o1, 103, "TP")
    # Now cap re-opens.
    assert router.route(_signal()).placed is True


# ---- journal + notifier side-effects ----------------------------------------


def test_journal_callback_fires_on_placement():
    calls = []
    broker = PaperBroker()
    router = SignalRouter(broker=broker, journal=lambda **kw: calls.append(kw))
    router.route(_signal())
    assert len(calls) == 1
    assert calls[0]["pair"] == "BTC/USDT:USDT"
    assert calls[0]["entry"] == "BUY"


def test_journal_callback_fires_on_rejection_with_marker():
    calls = []
    broker = PaperBroker()
    broker.place_order(Order(pair="BTC/USDT:USDT", side="BUY", entry=100, sl=99, tp=103, qty=1))
    router = SignalRouter(
        broker=broker,
        cap_gate=CapGate([MaxOpenPositions(1)]),
        journal=lambda **kw: calls.append(kw),
    )
    router.route(_signal())
    assert len(calls) == 1
    assert calls[0]["entry"].startswith("REJECTED")


def test_journal_failure_does_not_block_placement():
    broker = PaperBroker()

    def bad_journal(**_):
        raise RuntimeError("disk full")

    router = SignalRouter(broker=broker, journal=bad_journal)
    out = router.route(_signal())
    assert out.placed is True  # placement happened despite journal blowup
    assert len(broker.positions()) == 1


def test_notifier_callback_fires_on_placement():
    msgs = []
    broker = PaperBroker()
    router = SignalRouter(broker=broker, notifier=msgs.append)
    router.route(_signal())
    assert len(msgs) == 1
    assert "BUY BTC/USDT:USDT" in msgs[0]


def test_notifier_failure_does_not_block_placement():
    def bad_notify(_msg):
        raise RuntimeError("telegram down")

    broker = PaperBroker()
    router = SignalRouter(broker=broker, notifier=bad_notify)
    out = router.route(_signal())
    assert out.placed is True


def test_default_cap_gate_allows_everything_when_no_caps():
    # Constructing without a CapGate should still place orders.
    broker = PaperBroker()
    router = SignalRouter(broker=broker)
    assert router.route(_signal()).placed is True
