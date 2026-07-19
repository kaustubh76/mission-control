from ictbot.indicators.risk import calculate_rr


def test_1_to_1_rr():
    assert calculate_rr(entry=100, sl=99, tp=101) == 1.0


def test_1_to_3_rr():
    assert calculate_rr(entry=100, sl=99, tp=103) == 3.0


def test_short_side_rr():
    # SELL setup: SL above, TP below
    assert calculate_rr(entry=100, sl=101, tp=97) == 3.0


def test_zero_risk_returns_zero():
    assert calculate_rr(entry=100, sl=100, tp=110) == 0
