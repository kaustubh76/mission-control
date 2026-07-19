"""
Micro Fair Value Gap (FVG) on the 1m timeframe.

3-candle imbalance: the wick of candle[-3] doesn't overlap candle[-1].

BULLISH FVG: low[-1] > high[-3]   (gap up)
BEARISH FVG: high[-1] < low[-3]   (gap down)

E3 (ROADMAP §E3): with `mitigation_bars` set, scan the last
mitigation_bars + 2 candles for the most recent FVG formation. If any
subsequent bar within mitigation_bars of that formation traded back
into the gap, the imbalance is filled and we return NO FVG.
"""

import pandas as pd


def _has_bullish_gap(df: pd.DataFrame, i: int) -> tuple[bool, float, float]:
    """Bullish 3-bar FVG centred on bar `i` (the [-1] bar of the window)."""
    if i < 2:
        return False, 0.0, 0.0
    low_i = float(df["low"].iloc[i])
    high_i_2 = float(df["high"].iloc[i - 2])
    return low_i > high_i_2, high_i_2, low_i


def _has_bearish_gap(df: pd.DataFrame, i: int) -> tuple[bool, float, float]:
    if i < 2:
        return False, 0.0, 0.0
    high_i = float(df["high"].iloc[i])
    low_i_2 = float(df["low"].iloc[i - 2])
    return high_i < low_i_2, high_i, low_i_2


def _is_filled(
    df: pd.DataFrame, formed_at: int, side: str, lo: float, hi: float, window: int
) -> bool:
    """True if any bar within `window` bars after `formed_at` retraced
    into the gap (low,hi)."""
    end = min(len(df), formed_at + 1 + window)
    if side == "bullish":
        # Gap is between hi (= high of i-2) and lo (= low of i). Price fills
        # the gap when any subsequent bar's low <= hi (it dipped back in).
        return bool((df["low"].iloc[formed_at + 1 : end] <= hi).any())
    else:
        # Bearish: gap between lo (= low of i-2, the higher level) and hi
        # (= high of i, the lower level). Filled when any subsequent
        # bar's high >= lo.
        return bool((df["high"].iloc[formed_at + 1 : end] >= lo).any())


def get_micro_fvg(
    df: pd.DataFrame,
    bias: str,
    mitigation_bars: int | None = None,
    *,
    min_formation_time=None,
) -> str:
    """Return BULLISH FVG / BEARISH FVG / NO FVG.

    Without `mitigation_bars` this is the legacy "last 3 candles only" check.
    With it, we scan the last (mitigation_bars + 2) candles for an FVG that
    formed and is still un-filled within mitigation_bars of formation.

    `min_formation_time` forwards to `get_micro_fvg_range` so label and
    range stay in sync under the after-MSS gate (Phase C).
    """
    range_ = get_micro_fvg_range(
        df,
        bias,
        mitigation_bars=mitigation_bars,
        min_formation_time=min_formation_time,
    )
    if range_ is None:
        return "NO FVG"
    return "BULLISH FVG" if bias == "BULLISH" else "BEARISH FVG"


def get_micro_fvg_info(
    df: pd.DataFrame,
    bias: str,
    mitigation_bars: int | None = None,
    *,
    min_formation_time=None,
) -> dict | None:
    """Same scan as `get_micro_fvg_range` but returns a dict with
    range AND formation metadata, or None when no qualifying gap exists.

    Shape:
        {
          "low":              float,    # gap_low
          "high":             float,    # gap_high (always > low)
          "formation_index":  int,      # iloc index of the formation bar (= [-1] of the gap window)
          "formation_time":   Timestamp | None,
        }

    Phase D (Box 5) needs the formation index/time so the retest check
    can scan bars STRICTLY AFTER the bar that printed the gap. Returning
    a dict keeps room to add more metadata later without breaking
    every caller again.
    """
    if len(df) < 5:
        return None

    def _bar_time_after(i: int) -> bool:
        if min_formation_time is None:
            return True
        try:
            return df["time"].iloc[i] > min_formation_time
        except (KeyError, IndexError):
            return True

    def _time_at(i: int):
        try:
            return df["time"].iloc[i]
        except (KeyError, IndexError):
            return None

    # Legacy 3-bar path.
    if not mitigation_bars or mitigation_bars <= 0:
        last_i = len(df) - 1
        if not _bar_time_after(last_i):
            return None
        if bias == "BULLISH":
            low_i = float(df["low"].iloc[-1])
            high_i_2 = float(df["high"].iloc[-3])
            if low_i > high_i_2:
                return {
                    "low": high_i_2,
                    "high": low_i,
                    "formation_index": last_i,
                    "formation_time": _time_at(last_i),
                }
            return None
        if bias == "BEARISH":
            high_i = float(df["high"].iloc[-1])
            low_i_2 = float(df["low"].iloc[-3])
            if high_i < low_i_2:
                return {
                    "low": high_i,
                    "high": low_i_2,
                    "formation_index": last_i,
                    "formation_time": _time_at(last_i),
                }
            return None
        return None

    side = "bullish" if bias == "BULLISH" else "bearish"
    last = len(df) - 1
    earliest = max(2, last - mitigation_bars - 1)

    for i in range(last, earliest - 1, -1):
        if not _bar_time_after(i):
            continue
        if side == "bullish":
            ok, hi, lo = _has_bullish_gap(df, i)
        else:
            ok, hi, lo = _has_bearish_gap(df, i)
        if not ok:
            continue
        if _is_filled(df, i, side, lo, hi, mitigation_bars):
            continue
        return {
            "low": hi,
            "high": lo,
            "formation_index": i,
            "formation_time": _time_at(i),
        }
    return None


def get_micro_fvg_range(
    df: pd.DataFrame,
    bias: str,
    mitigation_bars: int | None = None,
    *,
    min_formation_time=None,
) -> tuple[float, float] | None:
    """Return (low, high) of the most-recent unfilled FVG in the bias
    direction, or None.

    For a BULLISH FVG: (low_of_gap, high_of_gap) = (high[i-2], low[i]).
        The gap floor (low_of_gap) is the natural structural anchor for
        a long stop — price closing below it invalidates the imbalance.
    For a BEARISH FVG: (low_of_gap, high_of_gap) = (high[i], low[i-2]).
        Conversely, a short stop sits just above the gap ceiling.

    Thin wrapper over `get_micro_fvg_info` that drops the formation
    metadata. Kept as the public surface for callers that only need
    SL anchoring (Box 7).

    `min_formation_time` (Phase C): the formation bar `i` must have
    `df.time[i] > min_formation_time` or the gap is skipped.
    """
    info = get_micro_fvg_info(
        df,
        bias,
        mitigation_bars=mitigation_bars,
        min_formation_time=min_formation_time,
    )
    if info is None:
        return None
    return info["low"], info["high"]
