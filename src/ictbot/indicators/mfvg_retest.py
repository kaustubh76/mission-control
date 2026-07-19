"""
MFVG retest — Box 5 of the canonical ICT flow.

After the micro FVG forms on the 1m entry frame, the canonical sequence
requires price to RE-ENTER the gap before an entry fires. This module
defines what "re-enter" means and exposes a single check the strategy
calls when `require_mfvg_retest` is on.

User-confirmed semantics (per Phase D design Q):
    A bar's CLOSE falls inside [gap_low, gap_high] (inclusive).
    Wick-pierce without close is not a retest. Stricter than "touch into
    the range" but matches what most ICT educators teach as a valid
    re-entry confirmation.

The retest bar must come AFTER the FVG formation bar — a candle can't
retest itself. We use strict `>` on the timestamp so the formation bar's
own close is never counted.
"""

from __future__ import annotations

import pandas as pd


def has_mfvg_retest(
    df: pd.DataFrame,
    fvg_low: float,
    fvg_high: float,
    formation_time,
) -> bool:
    """Return True if any bar strictly after `formation_time` closed
    inside the [fvg_low, fvg_high] range.

    Args:
        df: the 1m entry frame (must have a `time` column and `close`).
        fvg_low / fvg_high: the gap bounds from `get_micro_fvg_info`.
        formation_time: timestamp of the bar that printed the gap. None
                        means we can't enforce the after-formation
                        constraint, so the function falls back to
                        scanning every bar (same inert-gate pattern
                        Phase C uses for missing MSS time).

    Returns False when:
        - the FVG range is degenerate (low >= high), since no close
          can fall inside an empty range, OR
        - no qualifying bar exists in the frame yet, OR
        - `df` is empty or missing the `time` / `close` columns.
    """
    if fvg_low >= fvg_high:
        return False
    if df is None or len(df) == 0:
        return False
    if "close" not in df.columns:
        return False

    if formation_time is not None and "time" in df.columns:
        # Strict > so the formation bar's own close doesn't count as a
        # retest of itself.
        candidates = df[df["time"] > formation_time]
    else:
        candidates = df

    if candidates.empty:
        return False

    closes = candidates["close"]
    return bool(((closes >= fvg_low) & (closes <= fvg_high)).any())
