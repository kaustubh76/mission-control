"""Stability harness unit tests (scripts/strategy_stability.py): the grade thresholds
(score_arm), disjoint window segmentation, the stability-first ranking, and an offline
smoke that grades a synthetic universe end-to-end (no network)."""

from __future__ import annotations

import numpy as np

import scripts.strategy_stability as ss


def _windows(dds):
    return [{"a": 0, "b": 1, "n": 50, "dd": d, "pass": d < 0.25} for d in dds]


def _fr(passes):
    return {f"f{i}": {"dd": 0.1, "pass": p} for i, p in enumerate(passes)}


def _reg(bull=0.1, bear=0.1, chop=0.1):
    return {
        "BULL": {"dd": bull, "n": 50, "low_conf": False},
        "BEAR": {"dd": bear, "n": 50, "low_conf": False},
        "CHOP": {"dd": chop, "n": 50, "low_conf": False},
    }


def test_grade_robust():
    s = ss.score_arm(_windows([0.10, 0.12, 0.11]), _fr([True, True, True]), _reg(), {}, tpw=9.0)
    assert s["grade"] == "ROBUST"


def test_grade_unstable_on_segment_fail():
    # a segment with dd >= 25% fails → pass_rate < 1 and dd_max >= 0.25 → UNSTABLE
    s = ss.score_arm(_windows([0.10, 0.31, 0.12]), _fr([True, True]), _reg(bear=0.31), {}, tpw=9.0)
    assert s["grade"] == "UNSTABLE"
    assert s["pass_rate"] < 1.0 and s["dd_max"] >= 0.25


def test_grade_fragile_on_friction_flip():
    # tight, all segments pass, but a friction level flips → not ROBUST, still FRAGILE (marginal)
    s = ss.score_arm(_windows([0.10, 0.12]), _fr([True, False]), _reg(), {}, tpw=9.0)
    assert s["grade"] == "FRAGILE"
    assert s["friction_stable"] is False


def test_grade_fragile_on_wide_spread():
    # all pass, dd_max < 25% but the spread is wide → trustworthy enough but not tight → FRAGILE
    s = ss.score_arm(_windows([0.08, 0.20]), _fr([True, True]), _reg(bull=0.20), {}, tpw=9.0)
    assert s["grade"] == "FRAGILE"
    assert s["dd_spread"] >= 0.08


def test_grade_unstable_when_inactive():
    s = ss.score_arm(_windows([0.10, 0.11]), _fr([True, True]), _reg(), {}, tpw=3.0)
    assert s["grade"] == "UNSTABLE"  # < 7 trades/wk fails the active floor on every grade


def test_window_segments_are_disjoint():
    segs = ss.window_segments(n=2500, warmup=160)
    assert len(segs) >= 2
    for a, b in segs:
        assert a < b
    for (a, _), (a2, _) in zip(segs, segs[1:], strict=False):
        assert a < a2
    # disjoint: each segment starts at or after the previous one ends
    for (_, b), (a2, _) in zip(segs, segs[1:], strict=False):
        assert a2 >= b


def test_short_history_yields_no_segments():
    assert ss.window_segments(n=300, warmup=160) == []  # span < MIN_SEG


def test_stability_key_orders_robust_first_then_spread():
    rows = [
        {"arm": "u", "grade": "UNSTABLE", "dd_spread": 0.30},
        {"arm": "r1", "grade": "ROBUST", "dd_spread": 0.05},
        {"arm": "r2", "grade": "ROBUST", "dd_spread": 0.02},
        {"arm": "f", "grade": "FRAGILE", "dd_spread": 0.10},
    ]
    order = [r["arm"] for r in sorted(rows, key=ss._stability_key)]
    assert order == ["r2", "r1", "f", "u"]  # ROBUST (tightest first) → FRAGILE → UNSTABLE


def test_run_stability_offline_smoke():
    n, k = 1200, 8
    rng = np.arange(n)
    close = np.column_stack(
        [100 * (1 + 0.0006 * (j + 1)) ** rng * (1 + 0.03 * np.sin(rng / 18 + j)) for j in range(k)]
    )
    res = ss.run_stability(
        close, arms=["momentum_adaptive", "dual_momentum"], save=False, now_iso="t"
    )
    assert {r["arm"] for r in res} == {"momentum_adaptive", "dual_momentum"}
    for r in res:
        assert r["grade"] in ("ROBUST", "FRAGILE", "UNSTABLE")
        assert r["regimes"] and "windows" in r
    # render must not raise on real result dicts
    assert "STABILITY report" in ss.render_stability_report(close, res, now_iso="t")
