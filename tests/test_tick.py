"""Tests for tick-size aware rounding."""

from ictbot.indicators.tick import round_to_tick


def test_legacy_default_matches_round_2():
    assert round_to_tick(77441.345) == 77441.35
    assert round_to_tick(1.3456) == 1.35


def test_btc_class_tick_size_0_5():
    # BTC USDT-M perp tick is 0.5
    assert round_to_tick(77441.34, 0.5) == 77441.5
    assert round_to_tick(77441.24, 0.5) == 77441.0


def test_xrp_class_tick_size_0_0001():
    # Low-priced asset like XRP — must NOT round to 2dp
    assert round_to_tick(1.34567, 0.0001) == 1.3457
    assert round_to_tick(1.34564, 0.0001) == 1.3456


def test_tick_size_none_falls_back_to_2dp():
    assert round_to_tick(100.123456, None) == 100.12


def test_tick_size_zero_falls_back_to_2dp():
    assert round_to_tick(100.123456, 0) == 100.12
