"""
Phase D — Box 5 of the canonical flow: MFVG retest gate.

Two layers under test:

  1. `has_mfvg_retest` correctly enforces:
       - retest = a CLOSE inside [low, high] (close-based, not wick)
       - candidate bars must be STRICTLY AFTER formation_time
       - inert when formation_time is None / data is empty / range
         degenerate.

  2. `ICTProMaxStrategy.require_mfvg_retest` blocks entry when the
     retest hasn't happened, and surfaces the missing piece in
     diagnostics so the TG card shows it.
"""

from __future__ import annotations

import pandas as pd

from ictbot.indicators.mfvg_retest import has_mfvg_retest
from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _bar(t, close):
    """Bar with controlled close; OHLV padded to satisfy DataFrame shape."""
    return {
        "time": pd.Timestamp(t),
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": 1.0,
    }


def _df(rows):
    return pd.DataFrame(rows)


# --- Layer 1: has_mfvg_retest semantics ---------------------------------------


def test_close_inside_range_after_formation_returns_true():
    df = _df(
        [
            _bar("2026-01-01 04:55", 95),  # before formation
            _bar("2026-01-01 04:56", 105),  # at/after formation, outside
            _bar("2026-01-01 04:57", 102),  # AFTER formation, close=102 in [100,103]
        ]
    )
    assert (
        has_mfvg_retest(
            df, fvg_low=100, fvg_high=103, formation_time=pd.Timestamp("2026-01-01 04:56")
        )
        is True
    )


def test_close_outside_range_after_formation_returns_false():
    df = _df(
        [
            _bar("2026-01-01 04:56", 105),
            _bar("2026-01-01 04:57", 110),  # close=110 outside [100,103]
            _bar("2026-01-01 04:58", 108),
        ]
    )
    assert (
        has_mfvg_retest(
            df, fvg_low=100, fvg_high=103, formation_time=pd.Timestamp("2026-01-01 04:56")
        )
        is False
    )


def test_close_inside_range_BEFORE_formation_is_ignored():
    """Strict > on formation_time: even an inside-range close earlier
    must not count."""
    df = _df(
        [
            _bar("2026-01-01 04:55", 101),  # would-be retest if not for time
            _bar("2026-01-01 04:56", 105),  # formation bar
            _bar("2026-01-01 04:57", 105),  # later, but no inside-range close
        ]
    )
    assert (
        has_mfvg_retest(
            df, fvg_low=100, fvg_high=103, formation_time=pd.Timestamp("2026-01-01 04:56")
        )
        is False
    )


def test_close_AT_formation_time_is_ignored():
    """Formation bar's own close cannot retest itself (strict >)."""
    df = _df(
        [
            _bar("2026-01-01 04:56", 102),  # close=102 IS inside, time = formation
        ]
    )
    assert (
        has_mfvg_retest(
            df, fvg_low=100, fvg_high=103, formation_time=pd.Timestamp("2026-01-01 04:56")
        )
        is False
    )


def test_inclusive_edges_count_as_retest():
    """User-confirmed semantic: low <= close <= high (inclusive)."""
    df = _df([_bar("2026-01-01 04:57", 100)])  # exactly the gap_low
    assert (
        has_mfvg_retest(
            df, fvg_low=100, fvg_high=103, formation_time=pd.Timestamp("2026-01-01 04:56")
        )
        is True
    )
    df = _df([_bar("2026-01-01 04:57", 103)])  # exactly the gap_high
    assert (
        has_mfvg_retest(
            df, fvg_low=100, fvg_high=103, formation_time=pd.Timestamp("2026-01-01 04:56")
        )
        is True
    )


def test_degenerate_range_returns_false():
    df = _df([_bar("2026-01-01 04:57", 102)])
    assert has_mfvg_retest(df, fvg_low=100, fvg_high=100, formation_time=None) is False
    assert has_mfvg_retest(df, fvg_low=105, fvg_high=100, formation_time=None) is False


def test_empty_or_missing_columns_returns_false():
    assert has_mfvg_retest(pd.DataFrame(), 100, 103, None) is False
    df_no_close = pd.DataFrame({"time": [pd.Timestamp("2026-01-01")], "high": [102]})
    assert has_mfvg_retest(df_no_close, 100, 103, None) is False


def test_none_formation_time_scans_whole_frame():
    """Inert-gate behaviour: when we can't enforce after-formation,
    a single inside-range close anywhere in the frame is enough."""
    df = _df(
        [
            _bar("2026-01-01 04:55", 95),
            _bar("2026-01-01 04:56", 101),  # inside range
        ]
    )
    assert has_mfvg_retest(df, fvg_low=100, fvg_high=103, formation_time=None) is True


# --- Layer 2: strategy uses the retest as an entry gate -----------------------


def test_default_require_mfvg_retest_is_true():
    s = ICTProMaxStrategy()
    assert s.require_mfvg_retest is True


def test_strategy_blocks_entry_when_retest_missing(monkeypatch):
    """Monkey-patch all upstream gates to TRUE except retest, then
    assert the strategy returns NO ENTRY and diagnostics surface the
    missing retest as a blocker."""
    from ictbot.strategy import ict_pro_max as strat_mod

    monkeypatch.setattr(
        strat_mod,
        "get_micro_fvg_info",
        lambda df, bias, mitigation_bars=None, *, min_formation_time=None: {
            "low": 100,
            "high": 103,
            "formation_index": -1,
            "formation_time": pd.Timestamp("2026-01-01 12:00"),
        },
    )
    monkeypatch.setattr(
        strat_mod, "has_mfvg_retest", lambda df, fvg_low, fvg_high, formation_time: False
    )
    monkeypatch.setattr(strat_mod, "get_ltf_mss", lambda df, bias, mode="swing": "BULLISH MSS")
    monkeypatch.setattr(
        strat_mod,
        "get_ltf_mss_time",
        lambda df, bias, mode="swing": pd.Timestamp("2026-01-01 11:00"),
    )

    # Minimal frames to reach the evaluate body
    def _flat(n):
        return pd.DataFrame(
            [
                {
                    "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1.0,
                }
                for i in range(n)
            ]
        )

    s = ICTProMaxStrategy(require_mfvg_retest=True, require_fvg=False)
    out = s.evaluate(
        _flat(60),
        _flat(30),
        _flat(40),
        _flat(10),
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
    assert out["entry"] == "NO ENTRY"
    # Whichever direction the closest is, its blocker list must mention the retest.
    blockers = out["diagnostics"]["blockers"]
    assert any("MFVG not retested" in b for b in blockers), f"expected retest blocker in {blockers}"
