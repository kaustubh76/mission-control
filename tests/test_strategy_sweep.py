"""Parameter-sensitivity sweep (scripts/strategy_sweep.py): grid construction (exit<entry for
breakout, counts, exactly one default), stability-graded ranking that always includes the default,
the recommend-verdict logic + overfit-smell flag, and SIM-only (persists no verdicts). Offline —
synthetic matrix; conftest forbids network."""

from __future__ import annotations

import numpy as np

import scripts.strategy_sweep as sw
from ictbot.runtime import verdicts


def _close(n: int = 900, k: int = 8) -> np.ndarray:
    rng = np.arange(n)
    return np.column_stack(
        [100 * (1 + 0.0006 * (j + 1)) ** rng * (1 + 0.04 * np.sin(rng / 15 + j)) for j in range(k)]
    )


def test_breakout_grid_enforces_exit_lt_entry_and_one_default():
    cfgs = sw._breakout_configs()
    assert len(cfgs) == 22
    assert all(c["strat"].exit_lb < c["strat"].entry_lb for c in cfgs)
    assert sum(c["is_default"] for c in cfgs) == 1
    d = next(c for c in cfgs if c["is_default"])
    assert d["strat"].entry_lb == 20 and d["strat"].exit_lb == 5 and d["p"].rebal_bars == 3


def test_mean_reversion_grid_count_and_default():
    cfgs = sw._mean_reversion_configs()
    assert len(cfgs) == 24 and sum(c["is_default"] for c in cfgs) == 1
    d = next(c for c in cfgs if c["is_default"])
    assert d["strat"].window == 20 and d["strat"].threshold == 1.0 and d["p"].rebal_bars == 6


def test_momentum_fast_grid_count_and_default():
    cfgs = sw._momentum_fast_configs()
    assert len(cfgs) == 9 and sum(c["is_default"] for c in cfgs) == 1
    d = next(c for c in cfgs if c["is_default"])
    assert d["p"].lookback == 60 and d["p"].rebal_bars == 3


def test_grid_grid_count_and_default():
    cfgs = sw._grid_configs()
    assert len(cfgs) == 6 and sum(c["is_default"] for c in cfgs) == 1
    d = next(c for c in cfgs if c["is_default"])
    assert d["strat"].window == 50 and d["p"].rebal_bars == 6


def test_sweep_arm_ranks_and_includes_default():
    rows = sw.sweep_arm(_close(), "momentum_fast")
    assert len(rows) == 9
    assert sum(r["is_default"] for r in rows) == 1
    keys = [sw._sweep_key(r) for r in rows]
    assert keys == sorted(keys)  # ranked stability-first
    assert all(r["grade"] in ("ROBUST", "FRAGILE", "UNSTABLE") for r in rows)


def _ranked(default_grade, best_grade, *, best_overfit=0.0):
    return [
        {
            "label": "x",
            "is_default": False,
            "grade": best_grade,
            "dd_spread": 0.03,
            "dd_max": 0.12,
            "overfit_delta": best_overfit,
        },
        {
            "label": "d",
            "is_default": True,
            "grade": default_grade,
            "dd_spread": 0.12,
            "dd_max": 0.20,
            "overfit_delta": 0.0,
        },
    ]


def test_verdict_when_default_is_best():
    ranked = [
        {
            "label": "d",
            "is_default": True,
            "grade": "ROBUST",
            "dd_spread": 0.02,
            "dd_max": 0.1,
            "overfit_delta": 0.0,
        },
        {
            "label": "x",
            "is_default": False,
            "grade": "FRAGILE",
            "dd_spread": 0.10,
            "dd_max": 0.2,
            "overfit_delta": 0.0,
        },
    ]
    assert "already the most robust" in sw._verdict(ranked)


def test_verdict_when_challenger_beats_default():
    v = sw._verdict(_ranked("FRAGILE", "ROBUST"))
    assert "beats the default" in v and "better GRADE" in v


def test_verdict_flags_overfit_smell():
    assert "curve-fit smell" in sw._verdict(_ranked("FRAGILE", "ROBUST", best_overfit=0.20))


def test_report_renders():
    res = sw.run_sweep(_close(), arms=["momentum_fast"], save=False, now_iso="t")
    rpt = sw.render_sweep_report(_close(), res, now_iso="t")
    assert "parameter-sensitivity sweep" in rpt and "`momentum_fast`" in rpt and "⭐" in rpt


def test_sweep_persists_no_verdicts(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("sweep must never persist verdicts")

    monkeypatch.setattr(verdicts, "record", _boom)
    sw.run_sweep(
        _close(), arms=["momentum_fast"], save=True, report_path=tmp_path / "s.md", now_iso="t"
    )
    assert (tmp_path / "s.md").exists()  # only the report is written
