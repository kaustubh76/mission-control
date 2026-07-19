"""
Regression test for the confidence-calculation substring bug.

The legacy implementation used `"MSS" in ltf_mss` and `"FVG" in micro_fvg`
to award the MSS / FVG confidence bits. Those substring checks return
True for "NO MSS" and "NO FVG" too, so the bot was reporting confidence=100
on pairs that did NOT have an MSS or FVG. That false positive was
bypassing the off-session TG gate (which keys off confidence >= 100),
which is what the user noticed as "spam".

Tests are pure (no monkey-patching beyond the gate functions) — we
hand-craft a result where MSS is missing and confirm confidence drops
to the correct value.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _bar(t, c):
    return {
        "time": pd.Timestamp(t),
        "open": c,
        "high": c + 0.5,
        "low": c - 0.5,
        "close": c,
        "volume": 1.0,
    }


def _flat(n, c=100.0):
    return pd.DataFrame([_bar(f"2026-01-01 {i // 60:02d}:{i % 60:02d}", c) for i in range(n)])


def _session():
    return {
        "killzone_active": True,
        "india_time": "10:00",
        "tokyo_time": "13:30",
        "tokyo_status": "OPEN",
        "london_time": "05:30",
        "london_status": "OPEN",
        "newyork_time": "00:30",
        "newyork_status": "CLOSED",
        "active_session": "LONDON",
    }


def test_no_mss_does_NOT_award_the_mss_confidence_bit(monkeypatch):
    """The original `"MSS" in ltf_mss` check awarded the bit on
    "NO MSS" because the substring matches. After the fix, confidence
    must drop by 25 when MSS is missing."""
    from ictbot.strategy import ict_pro_max as strat_mod

    # All other gates pass — only MSS is missing.
    monkeypatch.setattr(
        strat_mod.ICTProMaxStrategy,
        "_get_htf_bias",
        lambda self, df: "BULLISH",
    )
    monkeypatch.setattr(
        strat_mod.ICTProMaxStrategy,
        "_get_ltf_bias",
        lambda self, df: "BULLISH",
    )
    monkeypatch.setattr(
        strat_mod, "get_ob_poi", lambda df, bias, mitigation_bars=None, tick_size=None: 100.0
    )
    monkeypatch.setattr(strat_mod, "get_ltf_poi", lambda df, bias, tick_size=None: 100.0)
    monkeypatch.setattr(strat_mod, "get_poi_tap", lambda df, poi, tolerance_frac=None: "POI TAPPED")
    monkeypatch.setattr(
        strat_mod, "is_mitigated", lambda df, poi, side="demand", retire_bars=None: False
    )
    monkeypatch.setattr(
        strat_mod, "get_ltf_mss", lambda df, bias, mode="swing": "NO MSS"
    )  # <-- the bug-trigger
    monkeypatch.setattr(strat_mod, "get_ltf_mss_time", lambda df, bias, mode="swing": None)
    monkeypatch.setattr(
        strat_mod,
        "get_micro_fvg_info",
        lambda df, bias, mitigation_bars=None, *, min_formation_time=None: {
            "low": 95,
            "high": 99,
            "formation_index": -1,
            "formation_time": None,
        },
    )
    monkeypatch.setattr(
        strat_mod, "has_mfvg_retest", lambda df, fvg_low, fvg_high, formation_time: True
    )
    monkeypatch.setattr(strat_mod, "get_delta", lambda df: 5.0)
    monkeypatch.setattr(strat_mod, "get_relative_delta", lambda df: 0.0)
    monkeypatch.setattr(strat_mod, "get_atr", lambda df, period=14: 0.5)

    s = ICTProMaxStrategy(require_fvg=False)
    out = s.evaluate(_flat(60), _flat(30), _flat(40), _flat(10), _session())

    # poi tap (+25) + NO mss (0, post-fix) + fvg-not-required (+25)
    # + delta_buy (+25) = 75. Pre-fix this was 100.
    assert out["confidence"] == 75, (
        f"confidence={out['confidence']} — the substring bug awarded the "
        f"MSS bit on 'NO MSS' if this is 100"
    )
    assert out["entry"] == "NO ENTRY"


def test_no_fvg_with_require_fvg_does_NOT_award_the_fvg_bit(monkeypatch):
    """Same bug for FVG: `"FVG" in "NO FVG"` is True. With require_fvg
    set, the bit must drop when FVG is missing."""
    from ictbot.strategy import ict_pro_max as strat_mod

    monkeypatch.setattr(
        strat_mod.ICTProMaxStrategy,
        "_get_htf_bias",
        lambda self, df: "BULLISH",
    )
    monkeypatch.setattr(
        strat_mod.ICTProMaxStrategy,
        "_get_ltf_bias",
        lambda self, df: "BULLISH",
    )
    monkeypatch.setattr(
        strat_mod, "get_ob_poi", lambda df, bias, mitigation_bars=None, tick_size=None: 100.0
    )
    monkeypatch.setattr(strat_mod, "get_ltf_poi", lambda df, bias, tick_size=None: 100.0)
    monkeypatch.setattr(strat_mod, "get_poi_tap", lambda df, poi, tolerance_frac=None: "POI TAPPED")
    monkeypatch.setattr(
        strat_mod, "is_mitigated", lambda df, poi, side="demand", retire_bars=None: False
    )
    monkeypatch.setattr(strat_mod, "get_ltf_mss", lambda df, bias, mode="swing": "BULLISH MSS")
    monkeypatch.setattr(strat_mod, "get_ltf_mss_time", lambda df, bias, mode="swing": None)
    monkeypatch.setattr(
        strat_mod,
        "get_micro_fvg_info",
        lambda df, bias, mitigation_bars=None, *, min_formation_time=None: None,
    )
    monkeypatch.setattr(
        strat_mod, "has_mfvg_retest", lambda df, fvg_low, fvg_high, formation_time: True
    )
    monkeypatch.setattr(strat_mod, "get_delta", lambda df: 5.0)
    monkeypatch.setattr(strat_mod, "get_relative_delta", lambda df: 0.0)
    monkeypatch.setattr(strat_mod, "get_atr", lambda df, period=14: 0.5)

    s = ICTProMaxStrategy(require_fvg=True)  # crucial — gates fvg bit
    out = s.evaluate(_flat(60), _flat(30), _flat(40), _flat(10), _session())

    # poi (+25) + mss (+25) + NO fvg (0) + delta (+25) = 75.
    assert out["confidence"] == 75


@pytest.mark.parametrize(
    "ltf_mss,expect_bit",
    [
        ("BULLISH MSS", True),
        ("BEARISH MSS", True),
        ("NO MSS", False),
    ],
)
def test_mss_bit_only_awards_on_real_mss_label(ltf_mss, expect_bit, monkeypatch):
    """Parametric: each MSS label gets the bit only if it's a real MSS."""
    from ictbot.strategy import ict_pro_max as strat_mod

    monkeypatch.setattr(
        strat_mod.ICTProMaxStrategy,
        "_get_htf_bias",
        lambda self, df: "BULLISH",
    )
    monkeypatch.setattr(
        strat_mod.ICTProMaxStrategy,
        "_get_ltf_bias",
        lambda self, df: "BULLISH",
    )
    monkeypatch.setattr(
        strat_mod, "get_ob_poi", lambda df, bias, mitigation_bars=None, tick_size=None: 100.0
    )
    monkeypatch.setattr(strat_mod, "get_ltf_poi", lambda df, bias, tick_size=None: 100.0)
    monkeypatch.setattr(strat_mod, "get_poi_tap", lambda df, poi, tolerance_frac=None: "POI TAPPED")
    monkeypatch.setattr(
        strat_mod, "is_mitigated", lambda df, poi, side="demand", retire_bars=None: False
    )
    monkeypatch.setattr(strat_mod, "get_ltf_mss", lambda df, bias, mode="swing": ltf_mss)
    monkeypatch.setattr(
        strat_mod,
        "get_ltf_mss_time",
        lambda df, bias, mode="swing": (
            pd.Timestamp("2026-01-01 10:00") if ltf_mss != "NO MSS" else None
        ),
    )
    monkeypatch.setattr(
        strat_mod,
        "get_micro_fvg_info",
        lambda df, bias, mitigation_bars=None, *, min_formation_time=None: {
            "low": 95,
            "high": 99,
            "formation_index": -1,
            "formation_time": None,
        },
    )
    monkeypatch.setattr(
        strat_mod, "has_mfvg_retest", lambda df, fvg_low, fvg_high, formation_time: True
    )
    monkeypatch.setattr(strat_mod, "get_delta", lambda df: 5.0)
    monkeypatch.setattr(strat_mod, "get_relative_delta", lambda df: 0.0)
    monkeypatch.setattr(strat_mod, "get_atr", lambda df, period=14: 0.5)

    s = ICTProMaxStrategy(require_fvg=False, require_fvg_after_mss=False)
    out = s.evaluate(_flat(60), _flat(30), _flat(40), _flat(10), _session())

    # poi + maybe_mss + fvg + delta. Without mss = 75; with mss = 100.
    expected = 100 if expect_bit else 75
    assert out["confidence"] == expected
