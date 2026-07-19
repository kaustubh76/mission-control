"""
Order Block (OB) detection — real-ICT version of POI.

A demand order block (BULLISH context) = the last bearish (red) candle
before a strong upward break of structure. Price often returns there to
absorb liquidity before continuing higher.

A supply order block (BEARISH context) = the last bullish (green) candle
before a strong downward break of structure.

Simplified detection:
  1. Find the most recent swing high/low using ictbot.indicators.structure.find_swings.
  2. For BULLISH bias: locate the most recent swing LOW. Walk backwards
     from there until we find a bearish (close<open) candle — that's the
     demand OB. The POI level is the candle's high (top of the body+wick).
  3. Mirror for BEARISH.
"""

import pandas as pd

from ictbot.indicators.structure import find_swings
from ictbot.indicators.tick import round_to_tick


def find_order_block(
    df: pd.DataFrame,
    bias: str,
    swing_lookback: int = 3,
    *,
    fib_filter: float | None = None,
    fib_lookback_bars: int = 20,
) -> dict | None:
    """Return the most recent order block for the given bias, or None.

    Returns dict with: kind ('DEMAND'|'SUPPLY'), top, bottom, index.

    `fib_filter` is the premium/discount gate from the canonical ICT flow
    (and the external SMC artifact reviewed in docs/findings_artifact_diff.md).
    When set (e.g. 0.5), the OB midpoint must sit in the correct half of
    the recent `fib_lookback_bars`-bar swing range:
      - BULLISH bias / DEMAND OB → midpoint must be at or below the Fib
        level (in the *discount* half).
      - BEARISH bias / SUPPLY OB → midpoint must be at or above the Fib
        level (in the *premium* half).
    OBs that fail the filter are skipped and the walk continues backward
    for an earlier valid one. Defaults to None = no filtering (legacy).
    """
    if len(df) < swing_lookback * 2 + 1:
        return None

    swings = find_swings(df, lookback=swing_lookback)
    if not swings:
        return None

    if bias == "BULLISH":
        # Most recent swing low — walk backwards for a bearish candle.
        target_kind = "LOW"
        opposite = lambda row: row["close"] < row["open"]  # bearish (red) candle
        ob_kind = "DEMAND"
    else:
        target_kind = "HIGH"
        opposite = lambda row: row["close"] > row["open"]  # bullish (green) candle
        ob_kind = "SUPPLY"

    matching = [s for s in swings if s.kind == target_kind]
    if not matching:
        return None
    pivot = matching[-1]

    # Pre-compute the Fib level once (constant across the walk).
    fib_level: float | None = None
    if fib_filter is not None:
        tail = df.tail(max(1, fib_lookback_bars))
        leg_high = float(tail["high"].max())
        leg_low = float(tail["low"].min())
        # Degenerate range (flat market) — skip the filter rather than
        # rejecting every OB on a zero-width leg.
        if leg_high > leg_low:
            fib_level = leg_low + (leg_high - leg_low) * float(fib_filter)

    # Walk backwards from pivot index looking for the opposite-color candle.
    for i in range(pivot.index, -1, -1):
        row = df.iloc[i]
        if not opposite(row):
            continue
        ob_top = float(round(row["high"], 6))
        ob_bottom = float(round(row["low"], 6))
        if fib_level is not None:
            midpoint = (ob_top + ob_bottom) / 2.0
            if ob_kind == "DEMAND" and midpoint > fib_level:
                continue  # OB is in the premium half — skip for a BULLISH setup
            if ob_kind == "SUPPLY" and midpoint < fib_level:
                continue  # OB is in the discount half — skip for a BEARISH setup
        return {
            "kind": ob_kind,
            "top": ob_top,
            "bottom": ob_bottom,
            "index": int(i),
        }
    return None


def get_ob_poi(
    df: pd.DataFrame,
    bias: str,
    mitigation_bars: int | None = None,
    tick_size: float | None = None,
    *,
    fib_filter: float | None = None,
    fib_lookback_bars: int = 20,
) -> float:
    """Return a single POI price level from the order block, or fall back
    to the recent low/high if no order block is detectable.

    Convention: for DEMAND OBs (BULLISH bias) return the OB top — that's
    the level price needs to retest to fill the imbalance. For SUPPLY OBs
    (BEARISH) return the OB bottom.

    E3 (ROADMAP §E3): if `mitigation_bars` is set and the OB has been
    tapped within mitigation_bars of detection, skip it and fall through
    to the swing-low/high fallback.

    Audit gap #4: tick_size threads through both the OB level and the
    fallback so low-priced assets (XRP, DOGE) get correct-precision POI.

    `fib_filter` (premium/discount gate, docs/findings_artifact_diff.md):
    when set, OBs in the wrong half of the recent swing leg are skipped
    inside `find_order_block`. The fallback level is unaffected — a
    fallback fires precisely because no valid OB was found, and the
    filter has already done its job upstream.
    """
    # Forward the filter kwargs only when actually enabled, so legacy
    # tests that monkey-patch `find_order_block` with a narrower
    # signature keep working when the feature is off (the default).
    if fib_filter is not None:
        ob = find_order_block(df, bias, fib_filter=fib_filter, fib_lookback_bars=fib_lookback_bars)
    else:
        ob = find_order_block(df, bias)
    if ob is not None and mitigation_bars and mitigation_bars > 0:
        # Use the existing mitigation helper. side="demand" for BULLISH
        # (tagged when bar.low <= top), "supply" for BEARISH.
        # J19 (audit gap #28): scope the tap search to bars AFTER the OB
        # was formed — pre-OB taps don't count toward retirement.
        from ictbot.indicators.mitigation import is_mitigated

        level = ob["top"] if ob["kind"] == "DEMAND" else ob["bottom"]
        side = "demand" if ob["kind"] == "DEMAND" else "supply"
        if is_mitigated(
            df,
            level,
            side=side,
            retire_bars=mitigation_bars,
            after_index=ob["index"],
        ):
            ob = None  # spent — fall through to fallback.

    if ob is None:
        # Fallback: recent low / high (same shape as the old POI engine).
        if bias == "BULLISH":
            return round_to_tick(float(df["low"].tail(20).min()), tick_size)
        return round_to_tick(float(df["high"].tail(20).max()), tick_size)

    if ob["kind"] == "DEMAND":
        return round_to_tick(float(ob["top"]), tick_size)
    return round_to_tick(float(ob["bottom"]), tick_size)
