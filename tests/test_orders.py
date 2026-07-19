"""
Order dataclass unit tests. Lives alongside the broker tests but
focuses purely on `realised_pnl_R` formula correctness.

Existing paper / live broker tests exercise the integration path
(broker fires on_close → router records R → account books delta).
This file exercises the formula in isolation so a regression in the
fee-subtraction math (Fix 2.F, plan: live P&L clean-up) is caught
without needing a broker mock.
"""

from __future__ import annotations

from ictbot.exec.orders import Order


def _filled_order(
    side="BUY", entry=100.0, sl=95.0, tp=110.0, close_price=110.0, qty=0.5, fees_paid=None
) -> Order:
    o = Order(pair="BTC/USDT:USDT", side=side, entry=entry, sl=sl, tp=tp, qty=qty)
    o.status = "FILLED"
    o.close_price = close_price
    o.fees_paid = fees_paid
    return o


def test_realised_pnl_R_returns_none_for_open_order():
    o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.0, sl=95.0, tp=110.0, qty=0.5)
    assert o.realised_pnl_R() is None


def test_realised_pnl_R_buy_tp_no_fees():
    """Legacy formula: (close - entry) / risk. No fees → identical to
    the pre-Fix-2.F bit-for-bit result."""
    o = _filled_order(side="BUY", entry=100.0, sl=95.0, close_price=110.0)
    assert o.realised_pnl_R() == 2.0


def test_realised_pnl_R_sell_sl_no_fees():
    o = _filled_order(side="SELL", entry=100.0, sl=105.0, close_price=105.0)
    assert o.realised_pnl_R() == -1.0


def test_realised_pnl_R_subtracts_fees_when_present():
    """Fix 2.F: fees_paid in quote currency translates to R via
    fees / (qty × risk_distance). At qty=0.5, risk=5.0, fees=$1.25 →
    fees_R = 1.25 / (0.5 × 5.0) = 0.5R subtracted from the gross +2.0R."""
    o = _filled_order(side="BUY", entry=100.0, sl=95.0, close_price=110.0, qty=0.5, fees_paid=1.25)
    assert o.realised_pnl_R() == 1.5


def test_realised_pnl_R_fees_amplify_a_loss():
    """A losing trade with fees should book worse than -1.0R."""
    o = _filled_order(side="BUY", entry=100.0, sl=95.0, close_price=95.0, qty=0.5, fees_paid=1.25)
    assert o.realised_pnl_R() == -1.5


def test_realised_pnl_R_zero_risk_returns_zero():
    """Defensive: entry == sl makes risk distance zero; formula must
    not divide by zero."""
    o = _filled_order(side="BUY", entry=100.0, sl=100.0, close_price=110.0)
    assert o.realised_pnl_R() == 0.0


def test_realised_pnl_R_zero_qty_returns_gross_r():
    """qty=0 would divide by zero in the fees_R term — gate prevents that
    by falling back to gross R."""
    o = _filled_order(side="BUY", entry=100.0, sl=95.0, close_price=110.0, qty=0.0, fees_paid=1.0)
    assert o.realised_pnl_R() == 2.0


def test_fees_paid_defaults_to_none_for_backwards_compat():
    """Old call sites that don't set fees_paid must continue to receive
    the legacy formula bit-for-bit so existing tests stay green."""
    o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.0, sl=95.0, tp=110.0, qty=1.0)
    assert o.fees_paid is None
