"""
Phase C — Box 4 of the canonical flow: the MFVG must form on a bar
with a timestamp strictly later than the MSS bar.

Two layers under test:

  1. `get_micro_fvg / get_micro_fvg_range` honour the
     `min_formation_time` keyword: gaps whose formation bar's time is
     at or before the threshold are skipped, equivalent to NO FVG.

  2. `ICTProMaxStrategy` populates the threshold from
     `get_ltf_mss_time(mss_frame, htf_bias)` when `require_fvg_after_mss`
     is on AND MSS has actually confirmed. A "FVG-but-no-MSS" bar leaves
     the gate inert (no threshold) so existing labels are unchanged.
"""

from __future__ import annotations

import pandas as pd

from ictbot.indicators.fvg import get_micro_fvg, get_micro_fvg_range
from ictbot.indicators.mss import get_ltf_mss_time
from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _bar(t, o, h, l, c, v=1.0):
    return {"time": pd.Timestamp(t), "open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows):
    return pd.DataFrame(rows)


# A bullish 3-bar FVG: high[-3]=100 < low[-1]=105 → gap (100, 105).
# Formation bar time = the last bar's `time`.
def _bullish_fvg_frame():
    """5 bars (get_micro_fvg requires >=5). Legacy 3-bar check uses
    iloc[-1] vs iloc[-3]. We engineer indices 2 and 4 so iloc[-3] =
    index 2 (high=100, the gap FLOOR) and iloc[-1] = index 4 (low=105,
    the gap CEILING). Bars 0/1/3 are filler.
    """
    return _df(
        [
            _bar("2026-01-01 04:54", 95, 99, 94, 98),  # i=0 filler
            _bar("2026-01-01 04:55", 98, 101, 97, 99),  # i=1 filler
            _bar("2026-01-01 04:56", 99, 100, 97, 99),  # i=2 iloc[-3] floor (high=100)
            _bar("2026-01-01 04:57", 102, 104, 101, 103),  # i=3 iloc[-2] mid
            _bar("2026-01-01 04:58", 105, 108, 105, 107),  # i=4 iloc[-1] ceiling (low=105)
        ]
    )


# --- Layer 1: indicator-level threshold check ---------------------------------


def test_no_threshold_means_legacy_behaviour():
    df = _bullish_fvg_frame()
    assert get_micro_fvg(df, "BULLISH") == "BULLISH FVG"
    assert get_micro_fvg_range(df, "BULLISH") is not None


def test_threshold_strictly_before_formation_passes():
    """If MSS happened before the formation bar, the FVG survives."""
    df = _bullish_fvg_frame()
    # Formation bar is iloc[-1] at 04:58. A threshold at 04:55 = strictly
    # before → gate accepts.
    assert (
        get_micro_fvg(df, "BULLISH", min_formation_time=pd.Timestamp("2026-01-01 04:55"))
        == "BULLISH FVG"
    )
    assert (
        get_micro_fvg_range(df, "BULLISH", min_formation_time=pd.Timestamp("2026-01-01 04:55"))
        is not None
    )


def test_threshold_at_formation_time_rejects():
    """`min_formation_time` is a strict-greater-than check, so an equal
    timestamp must reject."""
    df = _bullish_fvg_frame()
    # Formation bar is iloc[-1] at 04:58. Threshold at 04:58 → rejects.
    assert (
        get_micro_fvg(df, "BULLISH", min_formation_time=pd.Timestamp("2026-01-01 04:58"))
        == "NO FVG"
    )
    assert (
        get_micro_fvg_range(df, "BULLISH", min_formation_time=pd.Timestamp("2026-01-01 04:58"))
        is None
    )


def test_threshold_after_formation_rejects():
    df = _bullish_fvg_frame()
    assert (
        get_micro_fvg(df, "BULLISH", min_formation_time=pd.Timestamp("2026-01-01 05:00"))
        == "NO FVG"
    )


def test_threshold_works_in_mitigation_scan_path():
    """Same gating applies when mitigation_bars triggers the scan."""
    df = _bullish_fvg_frame()
    # 3 bars after formation is fine — gap not filled, not pre-mss-time
    assert (
        get_micro_fvg(
            df, "BULLISH", mitigation_bars=3, min_formation_time=pd.Timestamp("2026-01-01 04:55")
        )
        == "BULLISH FVG"
    )
    # Threshold at formation time → rejected on this path too
    assert (
        get_micro_fvg(
            df, "BULLISH", mitigation_bars=3, min_formation_time=pd.Timestamp("2026-01-01 04:58")
        )
        == "NO FVG"
    )


# --- Layer 2: strategy uses MSS time as the threshold -------------------------


def test_default_require_fvg_after_mss_is_true():
    """Phase C spec default. Legacy callers must opt out via False."""
    s = ICTProMaxStrategy()
    assert s.require_fvg_after_mss is True


def test_strategy_gates_fvg_by_mss_time(monkeypatch):
    """When MSS confirmed at time T, the strategy must pass T as the
    FVG floor. The strategy now calls get_micro_fvg_info (Phase D
    refactor) — we monkey-patch that to capture the threshold."""
    from ictbot.strategy import ict_pro_max as strat_mod

    captured = {}

    def fake_fvg_info(df, bias, mitigation_bars=None, *, min_formation_time=None):
        captured["min_formation_time"] = min_formation_time
        return None

    monkeypatch.setattr(strat_mod, "get_micro_fvg_info", fake_fvg_info)
    monkeypatch.setattr(strat_mod, "get_ltf_mss", lambda df, bias, mode="swing": "BULLISH MSS")
    monkeypatch.setattr(
        strat_mod,
        "get_ltf_mss_time",
        lambda df, bias, mode="swing": pd.Timestamp("2026-01-01 12:00"),
    )

    htf = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(60)])
    bias = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(30)])
    poi = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(40)])
    entry = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(10)])

    s = ICTProMaxStrategy(require_fvg_after_mss=True)
    s.evaluate(
        htf,
        bias,
        poi,
        entry,
        {
            "killzone_active": True,
            "india_time": "10:00",
            "tokyo_time": "13:30",
            "tokyo_status": "OPEN",
            "london_time": "05:30",
            "london_status": "OPEN",
            "newyork_time": "00:30",
            "newyork_status": "CLOSED",
            "active_session": "LONDON",
        },
    )

    assert captured.get("min_formation_time") == pd.Timestamp("2026-01-01 12:00")


def test_strategy_no_threshold_when_no_mss(monkeypatch):
    """If MSS hasn't confirmed, there's no time to compare against —
    the gate must be inert so the strategy doesn't treat
    'FVG-but-no-MSS' as 'no FVG either'."""
    from ictbot.strategy import ict_pro_max as strat_mod

    captured = {}

    def fake_fvg_info(df, bias, mitigation_bars=None, *, min_formation_time=None):
        captured["min_formation_time"] = min_formation_time
        return {
            "low": 100,
            "high": 103,
            "formation_index": -1,
            "formation_time": pd.Timestamp("2026-01-01 10:00"),
        }

    monkeypatch.setattr(strat_mod, "get_micro_fvg_info", fake_fvg_info)
    monkeypatch.setattr(strat_mod, "get_ltf_mss", lambda df, bias, mode="swing": "NO MSS")
    # If gate is inert, get_ltf_mss_time mustn't even be called — but
    # asserting that requires reading internals. Just check the
    # min_formation_time arrives as None.

    htf = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(60)])
    bias = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(30)])
    poi = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(40)])
    entry = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(10)])

    s = ICTProMaxStrategy(require_fvg_after_mss=True)
    s.evaluate(
        htf,
        bias,
        poi,
        entry,
        {
            "killzone_active": True,
            "india_time": "10:00",
            "tokyo_time": "13:30",
            "tokyo_status": "OPEN",
            "london_time": "05:30",
            "london_status": "OPEN",
            "newyork_time": "00:30",
            "newyork_status": "CLOSED",
            "active_session": "LONDON",
        },
    )

    assert captured.get("min_formation_time") is None


def test_get_ltf_mss_time_returns_last_bar_when_mss_confirmed():
    """Sanity check on the new helper."""
    df = pd.DataFrame(
        [
            _bar(f"2026-01-01 04:{50 + i:02d}", 100 + i, 101 + i, 99 + i, 100.5 + i)
            for i in range(10)
        ]
    )
    t = get_ltf_mss_time(df, "BULLISH", mode="simple")
    # Simple-MSS confirms when last_high > prev_high; ascending bars satisfy this.
    assert t == df["time"].iloc[-1]


def test_get_ltf_mss_time_none_when_no_mss():
    """A flat frame: simple-MSS = NO MSS, helper returns None."""
    df = pd.DataFrame([_bar("2026-01-01", 100, 101, 99, 100) for _ in range(10)])
    assert get_ltf_mss_time(df, "BULLISH", mode="simple") is None
