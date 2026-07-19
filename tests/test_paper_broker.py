"""Tests for the paper broker — Phase 8."""

from ictbot.exec.orders import Order
from ictbot.exec.paper import PaperBroker


def _order(side="BUY", entry=100, sl=95, tp=110, qty=1.0):
    return Order(pair="BTC/USDT:USDT", side=side, entry=entry, sl=sl, tp=tp, qty=qty)


def test_place_order_opens_immediately():
    b = PaperBroker()
    o = b.place_order(_order())
    assert o.status == "OPEN"
    assert o.filled_at is not None
    assert b.positions() == [o]


def test_on_bar_tp_hit_closes_long_with_TP_reason():
    b = PaperBroker()
    o = b.place_order(_order("BUY", entry=100, sl=95, tp=110))
    closed = b.on_bar("BTC/USDT:USDT", {"high": 112, "low": 99})
    assert closed == [o]
    assert o.status == "FILLED"
    assert o.close_price == 110
    assert o.close_reason == "TP"
    assert o.realised_pnl_R() == 2.0  # (110 - 100) / (100 - 95)


def test_on_bar_sl_hit_closes_short_with_SL_reason():
    b = PaperBroker()
    o = b.place_order(_order("SELL", entry=100, sl=105, tp=90))
    closed = b.on_bar("BTC/USDT:USDT", {"high": 106, "low": 100})
    assert closed == [o]
    assert o.status == "FILLED"
    assert o.close_price == 105
    assert o.close_reason == "SL"
    assert o.realised_pnl_R() == -1.0


def test_on_bar_does_nothing_when_range_inside_bracket():
    b = PaperBroker()
    o = b.place_order(_order("BUY", entry=100, sl=95, tp=110))
    closed = b.on_bar("BTC/USDT:USDT", {"high": 105, "low": 98})
    assert closed == []
    assert o.is_open()


def test_cancel_open_order():
    b = PaperBroker()
    o = b.place_order(_order())
    assert b.cancel(o.id) is True
    assert o.status == "CANCELLED"
    assert b.positions() == []
    # Cancelling twice is a no-op.
    assert b.cancel(o.id) is False
