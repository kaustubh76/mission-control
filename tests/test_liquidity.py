"""
Tests for the liquidity-level finder (next swing-high above / low below).

These cover the contract that the structural-TP path in ICTProMaxStrategy
will rely on: the "nearest unbroken swing" definition. A swing that's
been swept (any later bar closed through its price) is dead and must
not be returned as a TP target.
"""

from __future__ import annotations

import pandas as pd

from ictbot.indicators.liquidity import (
    get_next_liquidity_level,
    next_liquidity_above,
    next_liquidity_below,
)


def _bar(o, h, l, c, v=1.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows):
    return pd.DataFrame(rows)


# `find_swings` uses lookback=3 by default — a swing at index i needs
# 3 bars before AND 3 after that don't beat it. The helper frames below
# are sized accordingly.


def _flat_then_spike(spike_high: float, spike_index: int, n_bars: int) -> pd.DataFrame:
    """n_bars of low-volatility bars at price 100±2, with a single high
    `spike_high` at `spike_index`. Used to plant exactly one swing high."""
    rows = []
    for i in range(n_bars):
        if i == spike_index:
            rows.append(_bar(100, spike_high, 99, 100))
        else:
            rows.append(_bar(100, 102, 99, 100))
    return _df(rows)


# --- next_liquidity_above ----------------------------------------------------


def test_unbroken_swing_high_above_is_returned():
    """One swing high planted above current price; never breached → returned."""
    # 10 bars; swing high at index 4 (so it has 3 before + 5 after,
    # plenty for lookback=3 to find it).
    df = _flat_then_spike(spike_high=110, spike_index=4, n_bars=10)
    out = next_liquidity_above(df, current_price=100.0)
    assert out == 110.0


def test_no_swing_above_returns_none():
    """No bar ever prints a high above current price → no liquidity."""
    df = _flat_then_spike(spike_high=101, spike_index=4, n_bars=10)
    assert next_liquidity_above(df, current_price=105.0) is None


def test_swept_swing_high_is_skipped_for_unbroken_one():
    """A close-through sweep of the nearer swing forces the function to
    look at the next unbroken one. Two swings planted above price:
    closer (110) swept by a later close at 113, farther (115) remains
    unbroken → function returns 115.

    Bars between swings keep `find_swings`' lookback=3 window happy
    (each swing has 3+ same-or-lower bars on either side)."""
    rows = [
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 110, 99, 100),  # i=3 — closer swing at 110
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 115, 99, 113),  # i=7 — sweeps 110 AND is new swing at 115
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
    ]
    df = _df(rows)
    assert next_liquidity_above(df, current_price=100.0) == 115.0


def test_nearest_unbroken_swing_wins_over_farther_one():
    """Two unbroken swings above price → caller wants the closest."""
    rows = [
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 115, 99, 100),  # far swing high at 115
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 110, 99, 100),  # closer swing high at 110
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
    ]
    df = _df(rows)
    assert next_liquidity_above(df, current_price=100.0) == 110.0


def test_wick_pierce_without_close_does_not_count_as_swept():
    """The whole point of liquidity-sweep theory: a wick that pierces but
    bar closes back below = still live. We check close-through, not
    wick-through. The wick-pierce bar is placed far enough from the swing
    that its high doesn't fall inside the lookback window — otherwise
    find_swings would retroactively kill the original swing."""
    rows = [
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 110, 99, 100),  # i=3 — swing high at 110
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 111, 99, 108),  # i=10 — wick=111 but close=108 < 110
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
        _bar(100, 102, 99, 100),
    ]
    df = _df(rows)
    assert next_liquidity_above(df, current_price=100.0) == 110.0


# --- next_liquidity_below ----------------------------------------------------


def test_unbroken_swing_low_below_is_returned():
    rows = [_bar(100, 102, 99, 100) for _ in range(10)]
    # Plant a swing low at index 4
    rows[4] = _bar(100, 102, 90, 100)
    df = _df(rows)
    assert next_liquidity_below(df, current_price=100.0) == 90.0


def test_swept_swing_low_is_skipped_for_unbroken_one():
    """Mirror of the high test: nearer low (90) swept by close at 87,
    farther low (85) unbroken → function returns 85."""
    rows = [_bar(100, 102, 99, 100) for _ in range(11)]
    rows[3] = _bar(100, 102, 90, 100)  # nearer swing low at 90
    rows[7] = _bar(100, 102, 85, 87)  # closes 87 < 90 (sweeps 90) AND new swing at 85
    df = _df(rows)
    assert next_liquidity_below(df, current_price=100.0) == 85.0


# --- dispatcher --------------------------------------------------------------


def test_dispatch_routes_buy_to_above_and_sell_to_below():
    rows = [_bar(100, 102, 99, 100) for _ in range(10)]
    rows[3] = _bar(100, 110, 99, 100)  # swing high
    rows[6] = _bar(100, 102, 90, 100)  # swing low
    df = _df(rows)
    assert get_next_liquidity_level(df, "BUY", current_price=100.0) == 110.0
    assert get_next_liquidity_level(df, "SELL", current_price=100.0) == 90.0
    assert get_next_liquidity_level(df, "FLAT", current_price=100.0) is None


def test_empty_frame_returns_none():
    df = _df([])
    assert next_liquidity_above(df, 100.0) is None
    assert next_liquidity_below(df, 100.0) is None
    assert get_next_liquidity_level(df, "BUY", 100.0) is None
