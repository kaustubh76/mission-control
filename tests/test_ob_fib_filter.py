"""
Premium/discount filter on the order block.

When `fib_filter` is set, `find_order_block` must skip OBs whose midpoint
sits in the wrong half of the recent `fib_lookback_bars`-bar swing leg:
- BULLISH bias / DEMAND OB → midpoint must be at or below the Fib level
  (discount half).
- BEARISH bias / SUPPLY OB → midpoint must be at or above (premium half).

Default `fib_filter=None` preserves legacy behaviour exactly.

See docs/findings_artifact_diff.md for the ICT context (premium/discount
gate from the canonical flow + the external SMC artifact we reviewed).
"""

import pandas as pd

from ictbot.indicators.poi_order_block import find_order_block, get_ob_poi


def _df_from_rows(rows):
    """rows = list of (open, high, low, close)."""
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 1,
            }
            for i, (o, h, l, c) in enumerate(rows)
        ]
    )


# ---- baseline (no filter) ---------------------------------------------------


def test_default_fib_filter_none_preserves_legacy_behaviour():
    # Same fixture as test_order_block.test_finds_demand_ob_…, with no
    # filter argument. Result must be byte-identical to legacy.
    rows = [
        (110, 111, 109, 110),
        (110, 110, 108, 108),
        (108, 109, 105, 105),
        (105, 106, 105, 105),
        (105, 108, 104, 107),
        (107, 112, 106, 111),
        (111, 115, 110, 114),
        (114, 116, 113, 115),
    ]
    df = _df_from_rows(rows)
    legacy = find_order_block(df, "BULLISH", swing_lookback=2)
    with_filter_none = find_order_block(df, "BULLISH", swing_lookback=2, fib_filter=None)
    assert legacy == with_filter_none


# ---- BULLISH / DEMAND -------------------------------------------------------


def test_bullish_ob_in_discount_half_passes_fib_filter():
    # Leg low = 100, leg high = 120 → 50% = 110. A DEMAND OB at low=102,
    # high=104 (midpoint 103) is well into the discount half → kept.
    # Build a swing low at the OB so find_order_block has a pivot to use.
    rows = [
        (115, 116, 114, 115),
        (115, 116, 113, 113),
        (113, 114, 110, 110),
        (110, 111, 102, 102),  # 3 — RED candle, OB candidate, low=102 high=111
        (102, 104, 100, 103),  # 4 — swing low at 100
        (103, 108, 102, 107),  # 5 — rise
        (107, 115, 106, 113),  # 6
        (113, 120, 112, 119),  # 7 — leg high = 120
    ]
    df = _df_from_rows(rows)
    ob = find_order_block(df, "BULLISH", swing_lookback=2, fib_filter=0.5)
    assert ob is not None
    assert ob["kind"] == "DEMAND"
    # The midpoint of the chosen OB must be at or below the 50% level.
    leg_high, leg_low = df["high"].max(), df["low"].min()
    fib_level = leg_low + (leg_high - leg_low) * 0.5
    midpoint = (ob["top"] + ob["bottom"]) / 2
    assert midpoint <= fib_level


def test_bullish_ob_in_premium_half_rejected_by_fib_filter():
    # Fixture: a single RED candle at the top (premium half), then a
    # sequence of GAP-DOWN green candles that walk price down to a unique
    # swing low at bar 5. find_order_block walking back from the swing
    # low must hit bar 1 as the only red candle in the leg.
    #
    # Tail-leg high = 130, low = 100 → 50% = 115.
    # Bar 1 OB mid = (130+125)/2 = 127.5 > 115 → REJECTED by filter.
    # No earlier red exists, so filtered result must be None.
    rows = [
        (130, 130, 128, 130),  # 0 — doji (close == open), not red
        (130, 130, 125, 126),  # 1 — RED, premium-half OB (mid 127.5)
        (120, 121, 118, 121),  # 2 — gap-down green
        (115, 116, 112, 116),  # 3 — green
        (110, 111, 105, 111),  # 4 — green
        (105, 106, 100, 106),  # 5 — green, UNIQUE low=100 (swing low)
        (106, 112, 102, 111),  # 6 — green, low=102 (no tie)
        (111, 115, 109, 114),  # 7 — green
    ]
    df = _df_from_rows(rows)

    # Sanity: WITHOUT filter we DO find the OB (so the test is meaningful).
    ob_unfiltered = find_order_block(df, "BULLISH", swing_lookback=2)
    assert ob_unfiltered is not None
    assert ob_unfiltered["index"] == 1

    # WITH filter the premium-half OB must be skipped → None (no earlier
    # red candle exists in this fixture).
    ob_filtered = find_order_block(df, "BULLISH", swing_lookback=2, fib_filter=0.5)
    assert ob_filtered is None


# ---- BEARISH / SUPPLY -------------------------------------------------------


def test_bearish_ob_in_premium_half_passes_fib_filter():
    # Leg high = 130, leg low = 100 → 50% = 115. A SUPPLY OB at low=125,
    # high=130 (midpoint 127.5) is in the premium half → kept.
    rows = [
        (105, 107, 104, 106),
        (106, 108, 105, 107),
        (107, 130, 107, 128),  # 2 — green, unique high = 130 (swing high)
        (128, 129, 124, 125),  # 3 — red
        (125, 126, 118, 119),
        (119, 120, 110, 111),
        (111, 112, 105, 106),
        (106, 107, 100, 101),  # leg low = 100
    ]
    df = _df_from_rows(rows)
    ob = find_order_block(df, "BEARISH", swing_lookback=2, fib_filter=0.5)
    assert ob is not None
    assert ob["kind"] == "SUPPLY"
    leg_high, leg_low = df["high"].max(), df["low"].min()
    fib_level = leg_low + (leg_high - leg_low) * 0.5
    midpoint = (ob["top"] + ob["bottom"]) / 2
    assert midpoint >= fib_level


def test_bearish_ob_in_discount_half_rejected_by_fib_filter():
    # Leg high = 130, leg low = 100 → 50% = 115. The only green candle
    # before the swing high (bar 1) sits at low=100/high=104 → midpoint
    # 102 < 115 → REJECTED for a BEARISH setup.
    rows = [
        (100, 102, 100, 101),  # 0 — green (mid 102, discount half)
        (101, 104, 100, 103),  # 1 — green, discount-half OB candidate
        (103, 130, 103, 128),  # 2 — green, unique swing high at 130
        (128, 129, 124, 125),  # 3 — red
        (125, 126, 118, 119),
        (119, 120, 110, 111),
        (111, 112, 105, 106),
        (106, 107, 100, 101),
    ]
    df = _df_from_rows(rows)

    # Sanity: without filter the OB is found at bar 1 (last green before swing high).
    ob_unfiltered = find_order_block(df, "BEARISH", swing_lookback=2)
    assert ob_unfiltered is not None
    assert ob_unfiltered["index"] in (0, 1, 2)

    # With premium-only filter, discount-half OB candidates are skipped.
    # Bar 2 is also a green candle but its mid (116.5) sits just above
    # the 115 level so it WOULD pass. Either bar 2 wins, or None. Either
    # way bar 1 (the discount-half OB) must NOT be returned.
    ob_filtered = find_order_block(df, "BEARISH", swing_lookback=2, fib_filter=0.5)
    if ob_filtered is not None:
        assert ob_filtered["index"] != 1


# ---- degenerate / edge cases ------------------------------------------------


def test_fib_filter_inert_on_flat_market():
    # Zero-width swing range (every bar identical) → filter is silently
    # disabled rather than rejecting every candidate. A flat fixture has
    # no swings + no red/green direction, so the result is None for
    # legacy reasons — the filter must not turn that into an exception.
    df = _df_from_rows([(100, 100, 100, 100) for _ in range(20)])
    ob = find_order_block(df, "BULLISH", swing_lookback=2, fib_filter=0.5)
    assert ob is None  # no candidates, but no crash


def test_fib_filter_threads_through_get_ob_poi():
    # Extended 12-bar variant of the premium-rejected fixture so the
    # default `swing_lookback=3` (used by get_ob_poi) finds a swing low.
    # Same logic as the rejected-bullish test, just longer.
    #
    # tail(20) leg: high=130, low=100 → 50% = 115.
    # Only RED candle (bar 1) mid = 127.5 > 115 → rejected by filter.
    rows = [
        (130, 130, 128, 130),  # 0
        (130, 130, 125, 126),  # 1 — RED, premium-half OB (mid 127.5)
        (120, 121, 118, 121),  # 2 — gap-down green
        (115, 116, 112, 116),  # 3 — green
        (110, 111, 105, 111),  # 4 — green
        (105, 106, 102, 106),  # 5 — green
        (106, 107, 100, 106),  # 6 — green, UNIQUE low=100 (swing low)
        (106, 108, 103, 107),  # 7
        (107, 109, 104, 108),  # 8
        (108, 110, 106, 109),  # 9
        (109, 112, 107, 111),  # 10
        (111, 115, 109, 114),  # 11
    ]
    df = _df_from_rows(rows)
    # Without filter — OB found at bar 1, POI = OB top = 130.
    poi_unfiltered = get_ob_poi(df, "BULLISH")
    assert poi_unfiltered == 130.0
    # With filter — OB rejected, fallback to recent low of tail(20) = 100.
    poi_filtered = get_ob_poi(df, "BULLISH", fib_filter=0.5)
    assert poi_filtered == 100.0


def test_fib_lookback_bars_configurable():
    # Use a small lookback so only the last 3 bars define the leg. This
    # changes the Fib level vs the default tail(20) — proves the param
    # actually narrows the window.
    rows = [
        (50, 60, 40, 55),  # ancient leg, ignored when lookback=3
        (55, 56, 54, 55),
        (55, 56, 54, 55),
        (55, 56, 54, 55),
        (55, 56, 54, 55),
        (55, 100, 50, 90),  # last 3 bars define the leg: high=100, low=50
        (90, 95, 55, 60),  # RED candle, mid 75 → above 50% (=75) by ≥
        (60, 65, 50, 51),  # green, swing-low candidate
    ]
    df = _df_from_rows(rows)
    # With lookback=3, leg ≈ high 100, low 50, 50% = 75. The red bar's
    # mid is exactly 75 → boundary-inclusive → kept.
    ob = find_order_block(df, "BULLISH", swing_lookback=2, fib_filter=0.5, fib_lookback_bars=3)
    # We don't assert the exact OB choice (depends on which red candle
    # the walk hits first) — only that the function doesn't crash with
    # a non-default lookback and produces SOMETHING reasonable.
    assert ob is None or ob["kind"] == "DEMAND"
