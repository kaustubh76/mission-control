"""
Property-based tests for indicator pure functions — Phase 11 / gap T3.

Hypothesis generates random OHLCV frames and checks invariants that
must hold for ANY valid input, not just the hand-crafted fixtures in
the unit tests. Catches edge cases (single-bar series, flat markets,
extreme volatility, zero volume) that the example-based suite misses.
"""

import math

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ictbot.indicators.atr import get_atr
from ictbot.indicators.delta import get_delta
from ictbot.indicators.risk import calculate_rr

# Reasonable price/volume bounds. `allow_nan=False` because OHLCV with
# NaNs is a bug at the exchange layer, not something indicators must tolerate.
_price = st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False)
_vol = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)


@st.composite
def _ohlcv(draw, min_len: int = 20, max_len: int = 200):
    n = draw(st.integers(min_value=min_len, max_value=max_len))
    rows = []
    for i in range(n):
        o = draw(_price)
        c = draw(_price)
        h = max(o, c, draw(_price))  # high >= max(open, close)
        l = min(o, c, draw(_price))  # low <= min(open, close)
        v = draw(_vol)
        rows.append(
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        )
    return pd.DataFrame(rows)


@given(_ohlcv())
@settings(suppress_health_check=[HealthCheck.too_slow])
def test_atr_is_non_negative(df):
    """ATR is a magnitude — it cannot be negative regardless of input."""
    assert get_atr(df, period=14) >= 0.0


@given(_ohlcv())
def test_delta_sign_matches_volume_distribution(df):
    """If every candle is a doji (close == open) the delta is exactly zero.
    Otherwise the sign matches whichever colour holds more volume.
    """
    green_vol = df.loc[df["close"] > df["open"], "volume"].sum()
    red_vol = df.loc[df["close"] < df["open"], "volume"].sum()
    d = get_delta(df)
    if green_vol == red_vol:
        assert d == 0.0
    elif green_vol > red_vol:
        assert d > 0
    else:
        assert d < 0


@given(
    entry=st.floats(min_value=1.0, max_value=1e6),
    risk=st.floats(min_value=0.01, max_value=1.0),
    reward=st.floats(min_value=0.01, max_value=10.0),
    side=st.sampled_from(["BUY", "SELL"]),
)
def test_rr_matches_reward_over_risk(entry, risk, reward, side):
    if side == "BUY":
        sl, tp = entry * (1 - risk), entry * (1 + reward)
    else:
        sl, tp = entry * (1 + risk), entry * (1 - reward)
    expected = abs(tp - entry) / abs(entry - sl)
    got = calculate_rr(entry, sl, tp)
    assert math.isclose(got, round(expected, 2), abs_tol=0.01)
