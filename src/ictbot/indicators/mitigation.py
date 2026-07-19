"""
Mitigation tracking for POIs / order blocks / FVGs.

Real ICT: once price tags a level, it is *mitigated* — supply/demand
absorbed, imbalance filled, structure consumed — and the level is no
longer in play. The current strategy fires a "POI TAPPED" forever once
the price condition holds, which over-trades the same exhausted level.

This module gives the strategy a shared way to ask "has this level been
tapped within the recent window?" so each indicator can retire spent
zones. Phase 6 ships the helper; the strategy opts in via the
`mitigation_bars` parameter (None = legacy behaviour, no retirement).
"""

from __future__ import annotations

import pandas as pd


def first_tap_index(
    df: pd.DataFrame,
    level: float,
    side: str,
    after_index: int | None = None,
) -> int | None:
    """Return the index of the first bar that tagged `level` from the
    correct side, or None if untagged.

    - `side='demand'` (BULLISH POI / OB): tagged when bar.low <= level.
    - `side='supply'` (BEARISH POI / OB): tagged when bar.high >= level.

    J19 (audit gap #28): `after_index` restricts the search to bars
    strictly later than that index. For OB mitigation we want "was the
    OB tapped AFTER it formed?" — taps that predate the OB are
    irrelevant.
    """
    if side == "demand":
        mask = df["low"] <= level
    else:
        mask = df["high"] >= level
    if after_index is not None:
        # Only bars whose positional index > after_index can tap the OB.
        positions = list(range(len(df)))
        valid_mask = mask.to_numpy() & ([p > after_index for p in positions])
        hits = [p for p, ok in zip(positions, valid_mask, strict=False) if ok]
        return int(hits[0]) if hits else None
    hits = df.index[mask]
    return int(hits[0]) if len(hits) else None


def is_mitigated(
    df: pd.DataFrame,
    level: float,
    side: str,
    retire_bars: int,
    after_index: int | None = None,
) -> bool:
    """True if `level` was first tapped more than `retire_bars` ago.

    Returns False if the level has never been tapped. Returns True if
    the first tap happened > `retire_bars` bars before the current
    (last) bar — i.e. the level is "spent".

    J19 (audit gap #28): for OB mitigation pass `after_index=ob['index']`
    so taps that happened BEFORE the OB even existed don't retire it.
    Without this, deep-history OBs can be incorrectly classified as
    "mitigated" by taps that happened before the OB stopped being
    relevant.
    """
    if retire_bars <= 0:
        return False
    idx = first_tap_index(df, level, side, after_index=after_index)
    if idx is None:
        return False
    bars_since_tap = (len(df) - 1) - idx
    return bars_since_tap > retire_bars
