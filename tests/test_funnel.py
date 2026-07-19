"""
B3 (ROADMAP §B3) — widen the signal funnel.

Three changes:
  1. POI_TAP_TOLERANCE default 0.0015 → 0.005.
  2. require_fvg default True → False.
  3. New `delta_mode="relative"` knob: delta is regime-normalised.

Unit-level acceptance (does not run a 25k WFO — that's the empirical
acceptance bar). These tests verify the knobs do what the strategy
expects so the empirical run is reliable.
"""

import pandas as pd

from ictbot.indicators.delta import get_delta, get_relative_delta
from ictbot.settings import POI_TAP_TOLERANCE
from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _df(n, base=100.0, slope=0.0):
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=n, freq="1min"),
            "open": [base + i * slope for i in range(n)],
            "high": [base + i * slope + 0.5 for i in range(n)],
            "low": [base + i * slope - 0.5 for i in range(n)],
            "close": [base + i * slope + 0.1 for i in range(n)],
            "volume": [10.0] * n,
        }
    )


# ---- 1. defaults flipped ----------------------------------------------------


def test_poi_tap_tolerance_default_widened():
    # B3: 0.0015 → 0.005.
    assert POI_TAP_TOLERANCE == 0.005


def test_require_fvg_default_flipped_to_false():
    strat = ICTProMaxStrategy()
    assert strat.require_fvg is False


# ---- 2. relative-delta indicator -------------------------------------------


def test_get_relative_delta_zero_when_history_short():
    df = _df(5)
    assert get_relative_delta(df, window=20) == 0.0


def test_get_relative_delta_zero_when_all_volume_is_zero():
    df = _df(50)
    df["volume"] = 0.0
    assert get_relative_delta(df, window=20) == 0.0


def test_get_relative_delta_positive_when_last_bar_is_strong_buy():
    # 25 bars of moderate volume + a final bar with 10x volume on a green bar.
    df = _df(25)
    df.loc[df.index[-1], "close"] = df.loc[df.index[-1], "open"] + 1.0
    df.loc[df.index[-1], "volume"] = 100.0  # 10x normal
    rel = get_relative_delta(df, window=20)
    assert rel > 1.0


def test_get_relative_delta_negative_when_last_bar_is_strong_sell():
    df = _df(25)
    df.loc[df.index[-1], "close"] = df.loc[df.index[-1], "open"] - 1.0
    df.loc[df.index[-1], "volume"] = 100.0
    rel = get_relative_delta(df, window=20)
    assert rel < -1.0


def test_get_relative_delta_returns_scalar_float():
    df = _df(30)
    val = get_relative_delta(df)
    assert isinstance(val, float)


# ---- 3. strategy uses delta_mode -------------------------------------------


def test_strategy_records_delta_mode_in_result():
    strat = ICTProMaxStrategy(delta_mode="relative")
    htf = _df(60, slope=0.1)
    bias = _df(40)
    poi = _df(40)
    entry = _df(30)
    # Fake session — passing a stub dict that the strategy reads.
    session = {
        "india_time": "00:00:00",
        "tokyo_time": "00:00:00",
        "tokyo_status": "CLOSED",
        "london_time": "00:00:00",
        "london_status": "CLOSED",
        "newyork_time": "00:00:00",
        "newyork_status": "CLOSED",
        "active_session": "OFF",
        "killzone_active": False,
        "allow_trade": True,
    }
    r = strat.evaluate(htf, bias, poi, entry, session, pair="X")
    assert r["delta_mode"] == "relative"
    assert "relative_delta" in r


def test_strategy_default_delta_mode_is_sign():
    strat = ICTProMaxStrategy()
    assert strat.delta_mode == "sign"


def test_relative_delta_threshold_configurable():
    strat = ICTProMaxStrategy(delta_mode="relative", relative_delta_threshold=0.8)
    assert strat.relative_delta_threshold == 0.8


# ---- 4. backward compat: get_delta still works -----------------------------


def test_get_delta_still_returns_sum_buy_minus_sell():
    df = _df(10)
    df.loc[df.index[:5], "close"] = df.loc[df.index[:5], "open"] + 1  # buys
    df.loc[df.index[5:], "close"] = df.loc[df.index[5:], "open"] - 1  # sells
    delta = get_delta(df)
    # 5 buys at 10 vol - 5 sells at 10 vol = 0.
    assert delta == 0.0
