"""
Swing-based ICT structure: detect swing highs/lows and infer bias from
their sequence. Closer to real ICT than the SMA crossover in
ictbot.indicators.bias_sma.

A swing high at index i = bar i's high is the strict max of the
[i-N, i+N] window. Same definition mirrored for swing lows.

Bias rules:
  - Last two swing highs ASCENDING + last two swing lows ASCENDING → BULLISH
  - Last two swing highs DESCENDING + last two swing lows DESCENDING → BEARISH
  - Mixed (e.g. higher highs, lower lows) → use the most recent swing direction
"""

from dataclasses import dataclass
from typing import Literal

import pandas as pd


@dataclass
class Swing:
    index: int
    price: float
    kind: Literal["HIGH", "LOW"]


def find_swings(df: pd.DataFrame, lookback: int = 3) -> list[Swing]:
    """Return all swing points in chronological order."""
    swings: list[Swing] = []
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    for i in range(lookback, len(df) - lookback):
        window_h = highs[i - lookback : i + lookback + 1]
        window_l = lows[i - lookback : i + lookback + 1]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            swings.append(Swing(index=i, price=float(highs[i]), kind="HIGH"))
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            swings.append(Swing(index=i, price=float(lows[i]), kind="LOW"))
    return swings


def get_swing_bias(df: pd.DataFrame, lookback: int = 3) -> str:
    """Return 'BULLISH' or 'BEARISH' based on most recent two swings of each kind."""
    swings = find_swings(df, lookback=lookback)
    highs = [s for s in swings if s.kind == "HIGH"]
    lows = [s for s in swings if s.kind == "LOW"]

    if len(highs) < 2 or len(lows) < 2:
        # Fallback: compare first/last close
        if df["close"].iloc[-1] > df["close"].iloc[0]:
            return "BULLISH"
        return "BEARISH"

    higher_highs = highs[-1].price > highs[-2].price
    higher_lows = lows[-1].price > lows[-2].price

    if higher_highs and higher_lows:
        return "BULLISH"
    if (not higher_highs) and (not higher_lows):
        return "BEARISH"

    # Mixed — go with whichever swing is more recent
    last_swing = swings[-1]
    if last_swing.kind == "HIGH":
        return "BULLISH" if higher_highs else "BEARISH"
    else:
        return "BULLISH" if higher_lows else "BEARISH"
