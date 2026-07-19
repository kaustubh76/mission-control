from ictbot.indicators.delta import get_delta


def test_positive_delta_on_buy_pressure(buy_pressure_df):
    assert get_delta(buy_pressure_df) > 0


def test_negative_delta_on_sell_pressure(sell_pressure_df):
    assert get_delta(sell_pressure_df) < 0


def test_zero_delta_on_flat(flat_df):
    # flat_df: open == close on every row → no green, no red → delta = 0
    assert get_delta(flat_df) == 0.0
