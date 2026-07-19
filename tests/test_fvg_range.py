"""
Tests for get_micro_fvg_range — the (low, high) extractor that the
structural-SL path in ICTProMaxStrategy will anchor stops to.

Two invariants under test:

  1. Label-and-range agreement: whenever `get_micro_fvg` says
     "BULLISH FVG" / "BEARISH FVG", `get_micro_fvg_range` returns a
     concrete (low, high). When the label is "NO FVG", the range is
     None. They must never disagree, on either the legacy 3-bar path
     or the mitigation-bars scan path.

  2. Tuple orientation: returned tuple is always
     (low_of_gap, high_of_gap), regardless of direction. The strategy
     SL code anchors to range[0] for longs and range[1] for shorts.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ictbot.indicators.fvg import get_micro_fvg, get_micro_fvg_range


def _bar(o, h, l, c, v=1.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows):
    return pd.DataFrame(rows)


# --- Bullish 3-bar FVG --------------------------------------------------------


def _bullish_fvg_frame() -> pd.DataFrame:
    """5 bars where iloc[-3]/[-2]/[-1] form a bullish gap.

    Legacy 3-bar check uses iloc[-1] and iloc[-3]. With 5 bars in the
    frame those are indices 4 and 2. We want high[2]=100, low[4]=105 →
    gap (100, 105). Bars 0/1/3 are filler.
    """
    return _df(
        [
            _bar(95, 99, 94, 98),  # i=0  filler
            _bar(98, 101, 97, 99),  # i=1  filler
            _bar(99, 100, 97, 99),  # i=2  iloc[-3] — gap floor (high=100)
            _bar(102, 104, 101, 103),  # i=3  iloc[-2] (mid — irrelevant)
            _bar(105, 108, 105, 107),  # i=4  iloc[-1] — gap ceiling (low=105)
        ]
    )


def test_bullish_fvg_range_returns_low_then_high():
    df = _bullish_fvg_frame()
    rng = get_micro_fvg_range(df, "BULLISH")
    assert rng is not None
    low, high = rng
    # Lower edge of the gap = high[-3] = 100 (from the 2-bar back row).
    # Upper edge = low[-1] = 105.
    assert low == 100.0
    assert high == 105.0
    assert low < high  # invariant — low first, high second


def test_bullish_fvg_range_agrees_with_label():
    df = _bullish_fvg_frame()
    assert get_micro_fvg(df, "BULLISH") == "BULLISH FVG"
    assert get_micro_fvg_range(df, "BULLISH") is not None


# --- Bearish 3-bar FVG --------------------------------------------------------


def _bearish_fvg_frame() -> pd.DataFrame:
    """5 bars where iloc[-3]/[-2]/[-1] form a bearish gap.

    Legacy check: high[-1] < low[-3]. With 5 bars: low[i=2]=110,
    high[i=4]=105 → gap (105, 110). Bars 0/1/3 are filler.
    """
    return _df(
        [
            _bar(115, 118, 114, 116),  # i=0  filler
            _bar(113, 114, 112, 113),  # i=1  filler
            _bar(112, 113, 110, 111),  # i=2  iloc[-3] — gap ceiling (low=110)
            _bar(110, 111, 108, 109),  # i=3  iloc[-2] (mid — irrelevant)
            _bar(105, 105, 102, 103),  # i=4  iloc[-1] — gap floor (high=105)
        ]
    )


def test_bearish_fvg_range_returns_low_then_high():
    df = _bearish_fvg_frame()
    rng = get_micro_fvg_range(df, "BEARISH")
    assert rng is not None
    low, high = rng
    # Lower edge of the gap = high[-1] = 105. Upper edge = low[-3] = 110.
    assert low == 105.0
    assert high == 110.0
    assert low < high  # same orientation invariant for shorts


def test_bearish_fvg_range_agrees_with_label():
    df = _bearish_fvg_frame()
    assert get_micro_fvg(df, "BEARISH") == "BEARISH FVG"
    assert get_micro_fvg_range(df, "BEARISH") is not None


# --- No FVG ------------------------------------------------------------------


def test_no_fvg_returns_none():
    # Overlapping bars — no gap.
    df = _df(
        [
            _bar(100, 102, 98, 101),
            _bar(101, 103, 99, 102),
            _bar(102, 104, 100, 103),
            _bar(103, 105, 101, 104),
            _bar(104, 106, 102, 105),
        ]
    )
    assert get_micro_fvg(df, "BULLISH") == "NO FVG"
    assert get_micro_fvg_range(df, "BULLISH") is None
    assert get_micro_fvg(df, "BEARISH") == "NO FVG"
    assert get_micro_fvg_range(df, "BEARISH") is None


def test_too_few_bars_returns_none():
    df = _df([_bar(100, 101, 99, 100), _bar(101, 102, 100, 101)])
    assert get_micro_fvg_range(df, "BULLISH") is None
    assert get_micro_fvg(df, "BULLISH") == "NO FVG"  # parity


# --- Wrong-direction bias ----------------------------------------------------


def test_bullish_setup_with_bearish_bias_returns_none():
    """A bullish gap doesn't qualify for a SELL setup, even though the gap exists."""
    df = _bullish_fvg_frame()
    assert get_micro_fvg(df, "BEARISH") == "NO FVG"
    assert get_micro_fvg_range(df, "BEARISH") is None


# --- Mitigation path: filled FVG must not be reported ------------------------


def test_filled_bullish_gap_within_mitigation_window_returns_none():
    """Bar after the gap closes back into it — gap is no longer valid imbalance."""
    df = _df(
        [
            _bar(95, 99, 94, 98),
            _bar(98, 100, 97, 100),  # i=1, gap base = 100
            _bar(99, 102, 99, 101),  # i=2
            _bar(104, 107, 103, 106),  # i=3 (gap forms here: low=103 > high[i=1]=100)
            _bar(99, 101, 99, 100),  # i=4 — bar low=99 dips back below 100 → filled
        ]
    )
    # The legacy 3-bar check would look only at i=2,3,4 — the gap is from
    # i=2's high to i=4's low; that won't be a gap. So we use the scan path.
    assert get_micro_fvg_range(df, "BULLISH", mitigation_bars=5) is None


def test_unfilled_gap_in_mitigation_window_returns_range():
    """Bullish gap formed mid-window, never re-entered → range returned.

    The scan walks most-recent → oldest and stops at the first
    qualifying gap. So we need to:
      1. plant the gap we want at exactly one position (i=2: high[0]=100,
         low[2]=103),
      2. make bar i=1's high big enough that there's no second gap at
         i=3 (else the scan stops there first and returns the wrong
         range),
      3. keep all post-gap bars above 100 so the gap stays unfilled.
    """
    df = _df(
        [
            _bar(97, 100, 96, 99),  # i=0  high=100 → planned gap FLOOR
            _bar(101, 110, 101, 109),  # i=1  high=110 — blocks any i=3 gap
            _bar(104, 107, 103, 106),  # i=2  low=103 → planned gap CEILING
            _bar(106, 108, 105, 107),  # i=3  low=105, low[3] < high[1]=110 → no gap
            _bar(107, 109, 106, 108),  # i=4  low=106, low[4] < high[2]=107 → no gap
        ]
    )
    rng = get_micro_fvg_range(df, "BULLISH", mitigation_bars=5)
    assert rng is not None
    low, high = rng
    assert low == 100.0
    assert high == 103.0


# --- Label/range parity is the contract --------------------------------------


@pytest.mark.parametrize("bias", ["BULLISH", "BEARISH"])
def test_label_and_range_never_disagree_legacy(bias):
    """Whatever shape we feed in, label != "NO FVG" iff range is not None."""
    frames = [_bullish_fvg_frame(), _bearish_fvg_frame()]
    for df in frames:
        label = get_micro_fvg(df, bias)
        rng = get_micro_fvg_range(df, bias)
        if label == "NO FVG":
            assert rng is None
        else:
            assert rng is not None
            low, high = rng
            assert low < high
