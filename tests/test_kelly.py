"""
Tests for the Kelly criterion sizing in size.py.
"""

import pytest

from ictbot.engine.sizing import kelly_fraction, kelly_position_size


def test_kelly_classic_50_pct_at_1to1():
    # f* = p - (1-p)/b = 0.5 - 0.5/1 = 0 (no edge at 50% win-rate with 1:1)
    assert kelly_fraction(50, 1.0) == 0.0


def test_kelly_positive_edge():
    # 60% win-rate, 1:1 RR → f = 0.6 - 0.4/1 = 0.2 = 20%
    assert kelly_fraction(60, 1.0) == 0.2


def test_kelly_high_rr_low_winrate():
    # 30% win-rate, 1:3 RR → f = 0.3 - 0.7/3 = 0.3 - 0.2333 ≈ 0.0667
    assert abs(kelly_fraction(30, 3.0) - 0.066667) < 0.001


def test_kelly_negative_edge_clamps_to_zero():
    # 40% at 1:1 → f = 0.4 - 0.6 = -0.2 → clamps to 0
    assert kelly_fraction(40, 1.0) == 0.0


def test_kelly_position_size_half_kelly():
    d = kelly_position_size(balance=1000, win_rate_pct=60, rr=1.0)
    assert d["full_kelly_pct"] == 20.0
    assert d["half_kelly_pct"] == 10.0
    assert d["used_kelly_pct"] == 10.0  # half-kelly by default
    assert d["risk_usd"] == 100.0  # 10% of $1000


def test_kelly_position_size_full_kelly():
    d = kelly_position_size(balance=1000, win_rate_pct=60, rr=1.0, half=False)
    assert d["used_kelly_pct"] == 20.0
    assert d["risk_usd"] == 200.0


def test_kelly_with_entry_and_sl_returns_qty():
    d = kelly_position_size(balance=1000, win_rate_pct=60, rr=1.0, entry=100, sl=99)
    assert "qty" in d
    # half-kelly = 10% of 1000 = $100 risk, SL distance = 1 → qty = 100
    assert d["qty"] == 100.0
    assert d["notional"] == 10000.0


def test_kelly_invalid_inputs_raise():
    with pytest.raises(ValueError):
        kelly_fraction(-10, 1.0)
    with pytest.raises(ValueError):
        kelly_fraction(50, -1.0)
    with pytest.raises(ValueError):
        kelly_fraction(150, 1.0)
