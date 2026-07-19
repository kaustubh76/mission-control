"""
Phase 14.D — Unit tests for scripts/edge_check.py.

The script is in scripts/ (not src/), so we import it via importlib +
explicit path — same pattern as test_diagnose_live_pnl + test_status_script.

Focus areas:
  * _filter_broker_truth — only broker-truth rows make it in
  * _t_stat              — manual t-statistic formula
  * _normal_p_two_sided  — large-N p-value approximation
  * _verdict             — translates (n, mean, t) → verdict string
  * build_report         — end-to-end aggregation
  * exit codes           — 0 (real edge), 1 (pending), 2 (no truth)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "edge_check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("edge_check", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def edge_mod():
    return _load_module()


def _row(
    pair="BTC/USDT:USDT",
    outcome="WIN",
    broker="binance-live",
    pnl_r=1.0,
    closed_ts="2026-06-06T01:00:00+00:00",
):
    return {
        "pair": pair,
        "outcome": outcome,
        "broker": broker,
        "pnl_r": pnl_r,
        "closed_ts": closed_ts,
        "entry": "SELL",
    }


# ---- Filter --------------------------------------------------------------


class TestFilterBrokerTruth:
    def test_filters_paper_rows(self, edge_mod):
        rows = [
            _row(broker="paper"),
            _row(broker="binance-live"),
        ]
        out = edge_mod._filter_broker_truth(rows)
        assert len(out) == 1
        assert out[0]["broker"] == "binance-live"

    def test_filters_missing_pnl_r(self, edge_mod):
        rows = [
            _row(pnl_r=None),
            _row(pnl_r=2.5),
        ]
        out = edge_mod._filter_broker_truth(rows)
        assert len(out) == 1

    def test_filters_open_rows(self, edge_mod):
        rows = [
            _row(outcome="OPEN"),
            _row(outcome=None),
            _row(outcome="WIN"),
            _row(outcome="LOSS"),
            _row(outcome="BE"),
            _row(outcome="CLOSED"),
        ]
        out = edge_mod._filter_broker_truth(rows)
        # Only WIN/LOSS/BE/CLOSED count
        assert len(out) == 4


# ---- t-stat --------------------------------------------------------------


class TestTStat:
    def test_returns_none_for_n_less_than_two(self, edge_mod):
        assert edge_mod._t_stat([], 0.0) is None
        assert edge_mod._t_stat([1.0], 0.0) is None

    def test_returns_none_for_zero_variance(self, edge_mod):
        assert edge_mod._t_stat([1.0, 1.0, 1.0], 0.0) is None

    def test_matches_known_value(self, edge_mod):
        """A simple sanity check — n=4, mean=2, std=1 → t=4 against mu=0."""
        sample = [1.0, 2.0, 2.0, 3.0]  # mean=2.0, sample stdev=√(2/3)≈0.816
        # t = (2 - 0) / (0.816 / 2) = 2 / 0.408 = 4.9
        t = edge_mod._t_stat(sample, 0.0)
        assert t is not None
        assert t == pytest.approx(4.899, rel=0.01)

    def test_positive_mean_positive_t(self, edge_mod):
        t = edge_mod._t_stat([1.0, 1.5, 2.0, 0.5, 1.0], 0.0)
        assert t is not None
        assert t > 0

    def test_negative_mean_negative_t(self, edge_mod):
        t = edge_mod._t_stat([-1.0, -1.5, -2.0, -0.5, -1.0], 0.0)
        assert t is not None
        assert t < 0


# ---- p-value approx ------------------------------------------------------


class TestPValue:
    def test_t_equals_zero_p_one(self, edge_mod):
        # P(|Z| > 0) = 1
        assert edge_mod._normal_p_two_sided(0.0) == pytest.approx(1.0, rel=0.01)

    def test_t_two_p_around_005(self, edge_mod):
        # Two-sided p for |t|=2 in normal approx is 0.0455
        p = edge_mod._normal_p_two_sided(2.0)
        assert p == pytest.approx(0.0455, rel=0.05)

    def test_t_three_p_small(self, edge_mod):
        # |t|=3 → p ≈ 0.0027
        p = edge_mod._normal_p_two_sided(3.0)
        assert p < 0.01
        assert p > 0.001

    def test_returns_none_on_none(self, edge_mod):
        assert edge_mod._normal_p_two_sided(None) is None


# ---- Verdict -------------------------------------------------------------


class TestVerdict:
    def test_insufficient_when_below_one_third_min_n(self, edge_mod):
        # min_n=30 → < 10 = "insufficient data"
        assert "insufficient" in edge_mod._verdict(5, 1.0, 5.0, min_n=30)

    def test_no_signal_when_below_min_n(self, edge_mod):
        # n=20 < 30, even with strong t-stat
        assert "no signal yet" in edge_mod._verdict(20, 1.0, 5.0, min_n=30)

    def test_no_edge_when_t_below_two(self, edge_mod):
        # n=30, but t ≤ 2 → no edge
        assert "no edge" in edge_mod._verdict(30, 0.5, 1.5, min_n=30)

    def test_real_edge_when_t_above_two_and_positive(self, edge_mod):
        assert "REAL EDGE" in edge_mod._verdict(30, 0.8, 3.5, min_n=30)

    def test_negative_edge_when_t_below_minus_two(self, edge_mod):
        assert "NEGATIVE EDGE" in edge_mod._verdict(30, -0.8, -3.5, min_n=30)

    def test_none_t_when_zero_variance(self, edge_mod):
        # Zero variance with n above threshold
        assert "insufficient" in edge_mod._verdict(50, 1.0, None, min_n=30)


# ---- WFO baseline --------------------------------------------------------


class TestWfoBaseline:
    def test_falls_back_to_default_when_no_path(self, edge_mod):
        baseline = edge_mod._load_wfo_baseline(None)
        # Must contain the 4 active pairs from Phase 9.A scoreboard.
        for pair in ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT"):
            assert pair in baseline
        # SOL is the highest from the scoreboard.
        assert baseline["SOL/USDT:USDT"] == pytest.approx(0.80)

    def test_falls_back_to_default_when_path_missing(self, edge_mod):
        baseline = edge_mod._load_wfo_baseline(Path("/nonexistent.json"))
        # Still returns the hardcoded scoreboard.
        assert baseline["SOL/USDT:USDT"] == pytest.approx(0.80)

    def test_reads_user_supplied_json(self, edge_mod, tmp_path):
        import json

        custom = {
            "pairs": {
                "BTC/USDT:USDT": {"test": {"expectancy_R": 1.5}},
                "ETH/USDT:USDT": {"test": {"expectancy_R": -0.5}},
            }
        }
        p = tmp_path / "wfo.json"
        p.write_text(json.dumps(custom))
        baseline = edge_mod._load_wfo_baseline(p)
        assert baseline["BTC/USDT:USDT"] == pytest.approx(1.5)
        assert baseline["ETH/USDT:USDT"] == pytest.approx(-0.5)

    def test_skips_pairs_without_test_block(self, edge_mod, tmp_path):
        import json

        custom = {
            "pairs": {
                "BTC/USDT:USDT": {"verdict": "no edge"},  # missing test block
                "ETH/USDT:USDT": {"test": {"expectancy_R": 2.0}},
            }
        }
        p = tmp_path / "wfo.json"
        p.write_text(json.dumps(custom))
        baseline = edge_mod._load_wfo_baseline(p)
        assert "BTC/USDT:USDT" not in baseline
        assert baseline["ETH/USDT:USDT"] == pytest.approx(2.0)


# ---- build_report --------------------------------------------------------


class TestBuildReport:
    def test_empty_journal_returns_zero_truth(self, edge_mod):
        report = edge_mod.build_report(
            [],
            wfo_baseline=edge_mod.DEFAULT_WFO_TEST_EXPECTANCY,
        )
        assert report["broker_truth_count"] == 0
        assert report["per_pair"] == {}
        assert report["overall"]["n"] == 0

    def test_aggregates_by_pair(self, edge_mod):
        rows = [
            _row(pair="BTC/USDT:USDT", pnl_r=1.0),
            _row(pair="BTC/USDT:USDT", pnl_r=-1.0),
            _row(pair="ETH/USDT:USDT", pnl_r=2.5),
        ]
        report = edge_mod.build_report(
            rows,
            wfo_baseline=edge_mod.DEFAULT_WFO_TEST_EXPECTANCY,
        )
        assert report["per_pair"]["BTC/USDT:USDT"]["n"] == 2
        assert report["per_pair"]["BTC/USDT:USDT"]["mean_r"] == 0.0
        assert report["per_pair"]["ETH/USDT:USDT"]["n"] == 1
        assert report["per_pair"]["ETH/USDT:USDT"]["sum_r"] == pytest.approx(2.5)

    def test_n_one_verdict_is_insufficient(self, edge_mod):
        """N=1 (the current live state) — verdict must be insufficient."""
        rows = [_row(pair="XRP/USDT:USDT", pnl_r=5.019)]
        report = edge_mod.build_report(
            rows,
            wfo_baseline=edge_mod.DEFAULT_WFO_TEST_EXPECTANCY,
        )
        v = report["per_pair"]["XRP/USDT:USDT"]["verdict"]
        assert "insufficient" in v

    def test_strong_positive_with_large_n_flags_edge(self, edge_mod):
        """30 trades all at +1R → t-stat is ∞ (no variance) so we
        need actual variance. Simulate 30 trades, mean ~ +0.8R, std ~ 1R."""
        # Realistic pattern: 12 wins at +5R, 18 losses at -1R (38.9% WR,
        # +0.78R expectancy — matches Phase E WFO).
        rows = [_row(pair="BTC/USDT:USDT", pnl_r=5.0) for _ in range(12)] + [
            _row(pair="BTC/USDT:USDT", pnl_r=-1.0) for _ in range(18)
        ]
        report = edge_mod.build_report(
            rows,
            wfo_baseline=edge_mod.DEFAULT_WFO_TEST_EXPECTANCY,
            min_n=30,
        )
        s = report["per_pair"]["BTC/USDT:USDT"]
        assert s["n"] == 30
        assert s["mean_r"] > 0
        assert s["t_vs_zero"] > 2.0
        assert "REAL EDGE" in s["verdict"]


# ---- exit codes ----------------------------------------------------------


class TestExitCodes:
    def test_exit_2_when_no_truth(self, edge_mod, monkeypatch, tmp_path):
        # Empty journal → exit 2
        import sys

        empty = tmp_path / "signals.json"
        empty.write_text("[]")
        monkeypatch.setattr(sys, "argv", ["edge_check.py", "--journal", str(empty), "--json"])
        rc = edge_mod.main()
        assert rc == 2

    def test_exit_1_when_pending(self, edge_mod, monkeypatch, tmp_path):
        # Single broker-truth row → insufficient data → exit 1
        import json
        import sys

        rows = [_row(pair="XRP/USDT:USDT", pnl_r=5.019)]
        j = tmp_path / "signals.json"
        j.write_text(json.dumps(rows))
        monkeypatch.setattr(sys, "argv", ["edge_check.py", "--journal", str(j), "--json"])
        rc = edge_mod.main()
        assert rc == 1

    def test_exit_0_when_real_edge(self, edge_mod, monkeypatch, tmp_path):
        # Strong edge pattern → exit 0
        import json
        import sys

        rows = [_row(pair="BTC/USDT:USDT", pnl_r=5.0) for _ in range(12)] + [
            _row(pair="BTC/USDT:USDT", pnl_r=-1.0) for _ in range(18)
        ]
        j = tmp_path / "signals.json"
        j.write_text(json.dumps(rows))
        monkeypatch.setattr(sys, "argv", ["edge_check.py", "--journal", str(j), "--json"])
        rc = edge_mod.main()
        assert rc == 0
