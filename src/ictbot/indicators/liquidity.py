"""
External liquidity finder — the "next swing-high above" / "next swing-low
below" that's the canonical ICT TP2 target.

Reuses `structure.find_swings` so the swing definition stays consistent
across bias logic and target logic — a swing high used for bias is the
same swing high used for TP2.

Semantics:
    For a long entry, TP2 = the nearest UN-BROKEN swing high *above* the
    current price. "Unbroken" means no later bar closed through that high
    (which would mean liquidity was already swept). If every swing high
    in the lookback has been broken, return None — caller falls back to
    a fixed R-projection.

    Mirrored for shorts: nearest unbroken swing low below price.

The frame this is called on matters:
    - HTF (4h)  → catches macro liquidity pools (PWH, PWL, monthly highs).
    - LTF (3m)  → catches intraday liquidity (session highs/lows).
    The caller decides which timeframe makes sense for the setup it's
    closing into; this module is timeframe-agnostic.
"""

from __future__ import annotations

import pandas as pd

from ictbot.indicators.structure import find_swings


def next_liquidity_above(
    df: pd.DataFrame, current_price: float, *, lookback: int = 3
) -> float | None:
    """Nearest unbroken swing-high strictly above `current_price`, or None.

    "Unbroken" = no bar AFTER the swing point closed through the swing's
    price. We check close-through rather than wick-through because a wick
    that pierces and rejects is the textbook liquidity sweep — that level
    is still considered live until a body actually breaks it.
    """
    if len(df) == 0:
        return None
    swings = find_swings(df, lookback=lookback)
    highs_above = [s for s in swings if s.kind == "HIGH" and s.price > current_price]
    if not highs_above:
        return None

    closes = df["close"].to_numpy()
    # Sort by proximity to price (closest first) so the FIRST unbroken
    # match is automatically the nearest.
    highs_above.sort(key=lambda s: s.price - current_price)
    for s in highs_above:
        # Bars strictly after the swing formed
        later = closes[s.index + 1 :]
        if len(later) and (later >= s.price).any():
            continue  # swept
        return s.price
    return None


def next_liquidity_below(
    df: pd.DataFrame, current_price: float, *, lookback: int = 3
) -> float | None:
    """Nearest unbroken swing-low strictly below `current_price`, or None."""
    if len(df) == 0:
        return None
    swings = find_swings(df, lookback=lookback)
    lows_below = [s for s in swings if s.kind == "LOW" and s.price < current_price]
    if not lows_below:
        return None

    closes = df["close"].to_numpy()
    lows_below.sort(key=lambda s: current_price - s.price)
    for s in lows_below:
        later = closes[s.index + 1 :]
        if len(later) and (later <= s.price).any():
            continue
        return s.price
    return None


def get_next_liquidity_level(
    df: pd.DataFrame,
    direction: str,
    current_price: float,
    *,
    lookback: int = 3,
) -> float | None:
    """Direction-aware wrapper. `direction` = 'BUY' / 'SELL'.

    For BUY  → next unbroken swing high above current price (target).
    For SELL → next unbroken swing low below current price (target).
    Any other direction returns None.
    """
    if direction == "BUY":
        return next_liquidity_above(df, current_price, lookback=lookback)
    if direction == "SELL":
        return next_liquidity_below(df, current_price, lookback=lookback)
    return None
