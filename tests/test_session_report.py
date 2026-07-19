"""
Phase 16.D — Unit tests for scripts/session_report.py.

Coverage:
  * `_row_session` — prefers stored field, falls back to ts reconstruction
  * `_is_in_session` / `_in_date` — bucket boundary logic
  * `_t_stat` + `_welch_t` — stats helpers vs known references
  * `_bucket_stats` — n / mean / std / win-rate aggregation
  * `_classify_broker_truth` + `_classify_rejected` — filter semantics
  * `build_report` — end-to-end aggregation over a synthetic journal
  * `_render_markdown` — MD output contains the expected sections
  * `main()` — exit codes + --no-write + --out + invalid --date
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date as date_cls
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "session_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("session_report", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["session_report"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sr():
    return _load_module()


def _broker_truth_row(
    pair="BTC/USDT:USDT",
    pnl_r=1.0,
    outcome="WIN",
    ts="2026-06-06T10:00:00+00:00",  # 10:00 UTC — LONDON in BST
    closed_ts=None,
    session=None,
):
    return {
        "ts": ts,
        "pair": pair,
        "entry": "SELL",
        "outcome": outcome,
        "broker": "binance-live",
        "pnl_r": pnl_r,
        "closed_ts": closed_ts or ts,
        "close_reason": "TP" if outcome == "WIN" else "SL",
        "fees_paid": 0.01,
        "session": session,
    }


def _rejected_row(
    pair="BTC/USDT:USDT", reason="max_open_positions", ts="2026-06-06T10:00:00+00:00", session=None
):
    return {
        "ts": ts,
        "pair": pair,
        "entry": f"REJECTED ({reason} (1) reached)",
        "outcome": None,
        "session": session,
    }


# ---- _row_session --------------------------------------------------------


class TestRowSession:
    def test_prefers_stored_field(self, sr):
        row = _broker_truth_row(session="LONDON")
        assert sr._row_session(row) == "LONDON"

    def test_falls_back_to_ts_reconstruction(self, sr):
        # 10:00 UTC = London open in BST (June)
        row = _broker_truth_row(ts="2026-06-06T10:00:00+00:00", session=None)
        assert "LONDON" in sr._row_session(row).upper()

    def test_returns_unknown_for_missing_ts(self, sr):
        row = {"ts": None, "session": None}
        assert sr._row_session(row) == "UNKNOWN"

    def test_returns_unknown_for_malformed_ts(self, sr):
        row = {"ts": "not-a-timestamp", "session": None}
        assert sr._row_session(row) == "UNKNOWN"


# ---- _is_in_session ------------------------------------------------------


class TestIsInSession:
    def test_london_is_in(self, sr):
        assert sr._is_in_session("LONDON") is True

    def test_new_york_is_in(self, sr):
        assert sr._is_in_session("NEW YORK") is True

    def test_london_overlap_new_york_is_in(self, sr):
        # The composite label in sessions.py during overlap
        assert sr._is_in_session("LONDON / NEW YORK OVERLAP") is True

    def test_tokyo_is_off(self, sr):
        assert sr._is_in_session("TOKYO") is False

    def test_off_hours_is_off(self, sr):
        assert sr._is_in_session("OFF HOURS (24H CRYPTO)") is False

    def test_unknown_is_off(self, sr):
        assert sr._is_in_session("UNKNOWN") is False


# ---- _in_date ------------------------------------------------------------


class TestInDate:
    def test_matches_same_utc_date(self, sr):
        row = _broker_truth_row(ts="2026-06-06T23:59:59+00:00")
        assert sr._in_date(row, date_cls(2026, 6, 6)) is True

    def test_excludes_next_day_utc(self, sr):
        row = _broker_truth_row(ts="2026-06-07T00:00:01+00:00")
        assert sr._in_date(row, date_cls(2026, 6, 6)) is False


# ---- Stats helpers -------------------------------------------------------


class TestStats:
    def test_t_stat_known_value(self, sr):
        sample = [1.0, 2.0, 2.0, 3.0]
        t = sr._t_stat(sample, 0.0)
        assert t is not None
        assert t == pytest.approx(4.899, rel=0.01)

    def test_t_stat_none_on_small_sample(self, sr):
        assert sr._t_stat([], 0.0) is None
        assert sr._t_stat([1.0], 0.0) is None

    def test_t_stat_none_on_zero_variance(self, sr):
        assert sr._t_stat([2.0, 2.0, 2.0], 0.0) is None

    def test_welch_t_means_close(self, sr):
        # Identical samples → Welch's t = 0
        t = sr._welch_t([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        assert t == pytest.approx(0.0, abs=1e-9)

    def test_welch_t_positive_when_first_higher(self, sr):
        # in_session sample with mean ~+1 vs off_session ~-1 → t > 0
        # Use non-zero variance (Welch needs stdev > 0 per bucket).
        t = sr._welch_t([0.5, 1.0, 1.5, 1.0], [-0.5, -1.0, -1.5, -1.0])
        assert t is not None
        assert t > 2.0  # clear positive effect

    def test_welch_t_none_when_too_small(self, sr):
        assert sr._welch_t([1.0], [2.0]) is None

    def test_normal_p_two_sided_known(self, sr):
        p = sr._normal_p_two_sided(2.0)
        assert p == pytest.approx(0.0455, rel=0.05)


# ---- _bucket_stats -------------------------------------------------------


class TestBucketStats:
    def test_empty_returns_zeros(self, sr):
        b = sr._bucket_stats([])
        assert b["n"] == 0
        assert b["mean_r"] == 0.0
        assert b["sum_r"] == 0.0

    def test_aggregates_wins_and_losses(self, sr):
        rows = [
            _broker_truth_row(pnl_r=5.0, outcome="WIN"),
            _broker_truth_row(pnl_r=-1.0, outcome="LOSS"),
            _broker_truth_row(pnl_r=-1.0, outcome="LOSS"),
        ]
        b = sr._bucket_stats(rows)
        assert b["n"] == 3
        assert b["wins"] == 1
        assert b["losses"] == 2
        assert b["win_rate_pct"] == pytest.approx(33.3, rel=0.01)
        assert b["sum_r"] == pytest.approx(3.0)

    def test_fees_sum_aggregated(self, sr):
        rows = [
            {**_broker_truth_row(), "fees_paid": 0.04},
            {**_broker_truth_row(), "fees_paid": 0.05},
        ]
        b = sr._bucket_stats(rows)
        assert b["fees_usdt"] == pytest.approx(0.09)


# ---- Filters -------------------------------------------------------------


class TestFilters:
    def test_broker_truth_excludes_paper(self, sr):
        rows = [
            {**_broker_truth_row(), "broker": "paper"},
            _broker_truth_row(),
        ]
        out = sr._classify_broker_truth(rows)
        assert len(out) == 1

    def test_broker_truth_excludes_open(self, sr):
        rows = [
            {**_broker_truth_row(), "outcome": "OPEN"},
            _broker_truth_row(),
        ]
        out = sr._classify_broker_truth(rows)
        assert len(out) == 1

    def test_broker_truth_excludes_missing_pnl(self, sr):
        rows = [
            {**_broker_truth_row(), "pnl_r": None},
            _broker_truth_row(),
        ]
        out = sr._classify_broker_truth(rows)
        assert len(out) == 1

    def test_rejected_filter(self, sr):
        rows = [
            _broker_truth_row(),
            _rejected_row(reason="max_live_trades_per_day"),
            _rejected_row(reason="max_open_positions"),
        ]
        rejected = sr._classify_rejected(rows)
        assert len(rejected) == 2


# ---- build_report --------------------------------------------------------


class TestBuildReport:
    def test_empty_journal(self, sr):
        report = sr.build_report([], date_cls(2026, 6, 6))
        assert report["buckets"]["OVERALL"]["n"] == 0
        assert report["welch_t_in_vs_off"] is None
        assert report["per_pair_stats"] == {}

    def test_bucketing_by_session_field(self, sr):
        # Use stored session field to avoid timezone surprises
        rows = [
            _broker_truth_row(
                pair="BTC/USDT:USDT", pnl_r=2.0, session="LONDON", ts="2026-06-06T10:00:00+00:00"
            ),
            _broker_truth_row(
                pair="ETH/USDT:USDT", pnl_r=-1.0, session="TOKYO", ts="2026-06-06T03:00:00+00:00"
            ),
        ]
        r = sr.build_report(rows, date_cls(2026, 6, 6))
        assert r["buckets"]["IN_SESSION"]["n"] == 1
        assert r["buckets"]["OFF_SESSION"]["n"] == 1
        assert r["buckets"]["OVERALL"]["n"] == 2

    def test_only_rows_in_target_date(self, sr):
        rows = [
            _broker_truth_row(ts="2026-06-06T10:00:00+00:00", session="LONDON"),
            _broker_truth_row(ts="2026-06-05T10:00:00+00:00", session="LONDON"),
        ]
        r = sr.build_report(rows, date_cls(2026, 6, 6))
        assert r["buckets"]["OVERALL"]["n"] == 1

    def test_per_pair_stats_assembled(self, sr):
        rows = [
            _broker_truth_row(
                pair="BTC/USDT:USDT", pnl_r=2.0, session="LONDON", ts="2026-06-06T10:00:00+00:00"
            ),
            _broker_truth_row(
                pair="BTC/USDT:USDT", pnl_r=-1.0, session="TOKYO", ts="2026-06-06T03:00:00+00:00"
            ),
        ]
        r = sr.build_report(rows, date_cls(2026, 6, 6))
        assert r["per_pair_stats"]["BTC/USDT:USDT"]["IN_SESSION"]["n"] == 1
        assert r["per_pair_stats"]["BTC/USDT:USDT"]["OFF_SESSION"]["n"] == 1


# ---- Markdown rendering --------------------------------------------------


class TestMarkdown:
    def test_md_contains_required_sections(self, sr):
        rows = [
            _broker_truth_row(pnl_r=2.0, session="LONDON", ts="2026-06-06T10:00:00+00:00"),
        ]
        report = sr.build_report(rows, date_cls(2026, 6, 6))
        md = sr._render_markdown(report)
        for section in (
            "# Session-bucketed trade report",
            "## Hypothesis under test",
            "## Top-line by bucket",
            "## In-session vs off-session",
            "## Per-pair × bucket",
            "## Trade-by-trade",
            "## Cap rejections by bucket",
            "## Decision-quality note",
        ):
            assert section in md, f"missing section {section}"

    def test_empty_journal_md_clean(self, sr):
        """Empty journal still produces a valid-looking MD that says
        'Insufficient data' rather than crashing."""
        report = sr.build_report([], date_cls(2026, 6, 6))
        md = sr._render_markdown(report)
        assert "Insufficient data" in md

    def test_md_includes_welch_verdict_when_both_buckets_present(self, sr):
        # Use samples with non-zero variance so Welch's t is computable.
        in_rs = [0.5, 1.0, 1.5, 1.0, 0.5]
        off_rs = [-0.5, -1.0, -1.5, -1.0, -0.5]
        rows = [
            _broker_truth_row(pnl_r=r, session="LONDON", ts="2026-06-06T10:00:00+00:00")
            for r in in_rs
        ] + [
            _broker_truth_row(pnl_r=r, session="TOKYO", ts="2026-06-06T03:00:00+00:00")
            for r in off_rs
        ]
        report = sr.build_report(rows, date_cls(2026, 6, 6))
        md = sr._render_markdown(report)
        assert "Welch's t" in md
        # IN > OFF strongly → killzone hypothesis should be SUPPORTED
        assert "IN_SESSION edge LIKELY" in md


# ---- main() --------------------------------------------------------------


class TestMain:
    def test_no_write_prints_to_stdout(self, sr, tmp_path, monkeypatch, capsys):
        j = tmp_path / "signals.json"
        j.write_text(
            json.dumps([_broker_truth_row(session="LONDON", ts="2026-06-06T10:00:00+00:00")])
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "session_report.py",
                "--journal",
                str(j),
                "--date",
                "2026-06-06",
                "--no-write",
            ],
        )
        rc = sr.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Session-bucketed trade report" in out

    def test_writes_to_custom_path(self, sr, tmp_path, monkeypatch):
        j = tmp_path / "signals.json"
        j.write_text(
            json.dumps([_broker_truth_row(session="LONDON", ts="2026-06-06T10:00:00+00:00")])
        )
        out_path = tmp_path / "report.md"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "session_report.py",
                "--journal",
                str(j),
                "--date",
                "2026-06-06",
                "--out",
                str(out_path),
            ],
        )
        rc = sr.main()
        assert rc == 0
        assert out_path.exists()
        assert "Session-bucketed trade report" in out_path.read_text()

    def test_invalid_date_exits_two(self, sr, tmp_path, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "session_report.py",
                "--date",
                "not-a-date",
            ],
        )
        rc = sr.main()
        assert rc == 2
