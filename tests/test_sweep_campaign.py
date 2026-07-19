"""Tests for scripts/sweep_campaign.py — the campaign decision-driver.

`campaign_outcome` replays the 10%-halt + profit-lock ratchet over one equity
window; it drove the deployment config choice, so its halt/bank/trail/end logic
is verified here against hand-computed numpy segments. `window_stats` aggregates
it across rolling windows; `hard_gates` is the DQ/risk/active gate.

Loaded via importlib (it's a script, not a package module) — same pattern as
test_profit_lock.py. main() is __name__-guarded so importing runs no grid/network.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sweep_campaign.py"

# the production rail (matches scripts/sweep_campaign.py RULES + run_allocator defaults)
R = dict(dd_cap=0.10, trigger=0.05, trail=0.03, min_keep=0.03, bank=0.10)


@pytest.fixture(scope="module")
def sc():
    spec = importlib.util.spec_from_file_location("sweep_campaign_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seg(*vals):
    return np.asarray(vals, dtype=float)


# ------------------------------ campaign_outcome --------------------------- #
def test_outcome_end_when_trigger_never_reached(sc):
    # rises +3% < trigger 5%, never drops -> "end", no realized drawdown
    out, status, rdd = sc.campaign_outcome(seg(1000, 1010, 1020, 1030), **R)
    assert status == "end"
    assert out == pytest.approx(0.03)
    assert rdd == pytest.approx(0.0, abs=1e-9)


def test_outcome_arm_then_end(sc):
    # arms at +6%, drifts to +5.5% but stays above the floor -> "end"
    out, status, rdd = sc.campaign_outcome(seg(1000, 1060, 1055), **R)
    assert status == "end"
    assert out == pytest.approx(0.055)
    assert rdd == pytest.approx((1060 - 1055) / 1060)


def test_outcome_bank_at_target(sc):
    out, status, rdd = sc.campaign_outcome(seg(1000, 1060, 1105), **R)
    assert status == "bank"
    assert out == pytest.approx(0.105)


def test_outcome_bank_threshold_is_inclusive(sc):
    # cum exactly == bank banks (`cum >= bank`, not strict >). Use bank=0.5 with
    # nav 1500/anchor 1000 so cum == 0.5 EXACTLY in float (1.5-1.0), making the
    # >= vs > boundary a real (non-equivalent) assertion — +10% would be 0.1000…09.
    p = dict(R, bank=0.5)
    out, status, _ = sc.campaign_outcome(seg(1000, 1500), **p)
    assert status == "bank"
    assert out == pytest.approx(0.5)


def test_outcome_trail_after_giveback(sc):
    # arm 1060, peak 1080 (floor = max(1030, 1080*0.97=1047.6)), drop to 1040 < floor
    out, status, rdd = sc.campaign_outcome(seg(1000, 1060, 1080, 1040), **R)
    assert status == "trail"
    assert out == pytest.approx(0.04)
    assert rdd == pytest.approx((1080 - 1040) / 1080)


def test_outcome_halt_when_dd_exceeds_cap(sc):
    # -12% from the anchor peak -> dd 0.12 > 0.10 -> halt
    out, status, rdd = sc.campaign_outcome(seg(1000, 880), **R)
    assert status == "halt"
    assert out == pytest.approx(-0.12)
    assert rdd == pytest.approx(0.12)
    assert rdd > R["dd_cap"]  # halt ⇒ realized_dd strictly over the cap


def test_outcome_dd_cap_boundary_is_strict(sc):
    # dd exactly == cap (0.10) does NOT halt (strict >) -> rides to "end"
    out, status, rdd = sc.campaign_outcome(seg(1000, 900), **R)
    assert status == "end"
    assert rdd == pytest.approx(0.10)


def test_outcome_halt_wins_over_trail(sc):
    # armed + peak 1080; a -12% drop from peak both breaches dd_cap AND is below the
    # trail floor — the DD check runs first, so status is "halt", not "trail".
    out, status, _ = sc.campaign_outcome(seg(1000, 1060, 1080, 950), **R)
    assert status == "halt"  # dd=(1080-950)/1080=0.120 > 0.10
    assert out == pytest.approx(-0.05)


def test_outcome_bank_wins_over_trail(sc):
    # armed at 1060, next bar +11% (>=bank) — bank is checked before the armed-trail block
    out, status, _ = sc.campaign_outcome(seg(1000, 1060, 1110), **R)
    assert status == "bank"
    assert out == pytest.approx(0.11)


def test_outcome_min_keep_is_the_binding_floor(sc):
    # peak just above trigger (1055): trail-from-peak floor = 1023.35, but min_keep
    # floor = anchor*1.03 = 1030 dominates. 1029 < 1030 -> trail; 1031 >= 1030 -> safe.
    _, st_below, _ = sc.campaign_outcome(seg(1000, 1055, 1029), **R)
    assert st_below == "trail"
    _, st_above, _ = sc.campaign_outcome(seg(1000, 1055, 1031), **R)
    assert st_above == "end"


def test_outcome_realized_dd_is_max_experienced(sc):
    # dips -4% (below the 10% cap, no halt), recovers — realized_dd records the dip
    out, status, rdd = sc.campaign_outcome(seg(1000, 960, 1010), **R)
    assert status == "end"
    assert rdd == pytest.approx(0.04)  # (1000-960)/1000


# ------------------------------ window_stats ------------------------------- #
def test_window_stats_empty_when_too_short(sc, monkeypatch):
    monkeypatch.setattr(sc, "WARM", 0)
    assert sc.window_stats(seg(1000, 1010), win=5) == {"n_windows": 0}


def test_window_stats_aggregates_outcomes(sc, monkeypatch):
    monkeypatch.setattr(sc, "WARM", 0)
    monkeypatch.setattr(sc, "RULES", dict(R))
    # a clean monotone ramp: every 2-bar window banks (>=+10% over its own start)
    eq = seg(1000, 1120, 1260, 1400, 1580)
    s = sc.window_stats(eq, win=2)
    assert s["n_windows"] >= 1
    assert s["p_banked"] == pytest.approx(1.0)
    assert s["p_halted"] == pytest.approx(0.0)
    assert s["worst_realized_dd"] == pytest.approx(0.0, abs=1e-9)
    assert 0.0 <= s["p_outcome_ge_5"] <= 1.0
    assert set(s) >= {
        "p_outcome_ge_5",
        "p_halted",
        "worst_outcome",
        "worst_realized_dd",
        "p_dd_over_30_raw",
    }


def test_window_stats_counts_a_halt(sc, monkeypatch):
    monkeypatch.setattr(sc, "WARM", 0)
    monkeypatch.setattr(sc, "RULES", dict(R))
    # one window crashes -20% -> a halt shows up in the distribution
    eq = seg(1000, 800, 800, 800)
    s = sc.window_stats(eq, win=2)
    assert s["n_windows"] >= 1
    assert s["p_halted"] > 0.0
    assert s["worst_realized_dd"] >= 0.10


# ------------------------------ hard_gates --------------------------------- #
def _good_stat():
    return {
        "n_windows": 100,
        "worst_realized_dd": 0.13,
        "p_dd_over_30_raw": 0.0,
        "p_outcome_ge_5": 0.2,
    }


def test_hard_gates_pass(sc):
    assert sc.hard_gates(_good_stat(), trades_wk=16.0, dd_cap=0.10) is True


def test_hard_gates_fail_on_realized_dd_over_budget(sc):
    s = _good_stat()
    s["worst_realized_dd"] = 0.16  # > dd_cap+0.05 = 0.15
    assert sc.hard_gates(s, trades_wk=16.0, dd_cap=0.10) is False


def test_hard_gates_fail_on_raw_dq_breach(sc):
    s = _good_stat()
    s["p_dd_over_30_raw"] = 0.01  # any window over the 30% DQ line
    assert sc.hard_gates(s, trades_wk=16.0, dd_cap=0.10) is False


def test_hard_gates_fail_on_too_few_trades(sc):
    assert sc.hard_gates(_good_stat(), trades_wk=6.0, dd_cap=0.10) is False


def test_hard_gates_fail_on_no_windows(sc):
    assert sc.hard_gates({"n_windows": 0}, trades_wk=16.0, dd_cap=0.10) is False
