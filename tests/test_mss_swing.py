"""Tests for the new mode='swing' MSS rule (Phase 6 / gap S2)."""

import pandas as pd

from ictbot.indicators.mss import get_ltf_mss


def _df(rows):
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 10,
            }
            for i, (o, h, l, c) in enumerate(rows)
        ]
    )


def test_swing_mss_requires_break_of_recent_swing_high():
    # Build a clear swing high at ~index 6, then drift back down, then break it.
    rows = []
    for i in range(15):
        # Tiny baseline movement.
        rows.append((100.0, 100.5, 99.5, 100.0))
    # Make index 6 a strict-max swing high (high=110, surrounded by lower bars).
    rows[6] = (100, 110, 99.5, 100)
    # Final bar closes ABOVE 110 — that's a BULLISH MSS.
    rows[-1] = (100, 112, 99.5, 111)

    df = _df(rows)
    assert get_ltf_mss(df, "BULLISH", mode="swing") == "BULLISH MSS"


def test_swing_mss_negative_when_close_does_not_break():
    rows = []
    for i in range(15):
        rows.append((100.0, 100.5, 99.5, 100.0))
    rows[6] = (100, 110, 99.5, 100)  # swing high at 110
    rows[-1] = (100, 100.5, 99.5, 100)  # close 100 < 110

    df = _df(rows)
    assert get_ltf_mss(df, "BULLISH", mode="swing") == "NO MSS"


def test_simple_mode_unchanged_after_refactor():
    # Same input the old test exercised — 2-bar rule still wins for "simple".
    rows = [(100, 101, 99, 100)] * 10
    rows[-2] = (100, 102, 99, 100)
    rows[-1] = (100, 105, 99, 100)
    df = _df(rows)
    assert get_ltf_mss(df, "BULLISH") == "BULLISH MSS"
    assert get_ltf_mss(df, "BULLISH", mode="simple") == "BULLISH MSS"
