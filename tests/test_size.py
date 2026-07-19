"""
Tests for size.position_size.
"""

import pytest

from ictbot.engine.sizing import position_size


def test_basic_long_setup():
    # $1000 balance, 1% risk = $10 max loss.
    # SL distance = 1 → qty = 10/1 = 10.
    d = position_size(balance=1000, risk_pct=1, entry=100, sl=99)
    assert d["risk_usd"] == 10.0
    assert d["sl_distance"] == 1
    assert d["qty"] == 10.0
    assert d["notional"] == 1000.0


def test_short_setup_uses_abs_distance():
    # SELL: entry=100, sl=101, distance = 1
    d = position_size(balance=1000, risk_pct=1, entry=100, sl=101)
    assert d["sl_distance"] == 1
    assert d["qty"] == 10.0


def test_smaller_risk_pct_gives_smaller_qty():
    a = position_size(balance=1000, risk_pct=1, entry=100, sl=99)
    b = position_size(balance=1000, risk_pct=0.5, entry=100, sl=99)
    assert b["qty"] == a["qty"] / 2


def test_wider_sl_gives_smaller_qty():
    # Doubling SL distance halves position size for same risk
    narrow = position_size(balance=1000, risk_pct=1, entry=100, sl=99)
    wide = position_size(balance=1000, risk_pct=1, entry=100, sl=98)
    assert wide["qty"] == narrow["qty"] / 2


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        position_size(balance=0, risk_pct=1, entry=100, sl=99)
    with pytest.raises(ValueError):
        position_size(balance=1000, risk_pct=0, entry=100, sl=99)
    with pytest.raises(ValueError):
        position_size(balance=1000, risk_pct=1, entry=100, sl=100)  # equal
    with pytest.raises(ValueError):
        position_size(balance=1000, risk_pct=1, entry=-100, sl=99)


def test_sl_pct_calculation():
    # SL 99 vs entry 100 = 1% away
    d = position_size(balance=1000, risk_pct=1, entry=100, sl=99)
    assert d["sl_pct"] == 1.0
