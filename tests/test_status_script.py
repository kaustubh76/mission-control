"""
Fix 13.C — unit tests for scripts/status.py.

Mocks the broker + journal so the script runs without touching
Binance or the on-disk journal. Verifies the 5 sections are all
present in the JSON output and that the human-readable variant
doesn't crash on the common shapes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "status.py"


def _load_status_module():
    spec = importlib.util.spec_from_file_location("status_mod", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["status_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def status_mod():
    return _load_status_module()


@pytest.fixture
def fake_broker():
    """Broker mock that pretends to be flat with $9921 equity."""
    b = MagicMock()
    b.equity.return_value = 9921.51
    b._client.fetch_positions.return_value = []
    return b


@pytest.fixture
def fake_diagnose_mod():
    """Mock the diagnose_live_pnl module — just return a known smoke
    gate result."""
    m = MagicMock()
    m.build_smoke_gate.return_value = {
        "pairs_passed": ["XRP/USDT:USDT"],
        "pairs_pending": ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"],
        "smoke_gate_pass": False,
        "per_pair": {
            "BTC/USDT:USDT": {"truth_count": 0, "first_close_ts": None},
            "ETH/USDT:USDT": {"truth_count": 0, "first_close_ts": None},
            "SOL/USDT:USDT": {"truth_count": 0, "first_close_ts": None},
            "XRP/USDT:USDT": {
                "truth_count": 1,
                "first_close_ts": "2026-06-06T04:37:30+00:00",
            },
        },
    }
    return m


PAIRS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT"]


class TestBuildStatus:
    def test_all_five_sections_present(self, status_mod, fake_broker, fake_diagnose_mod):
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=[],
        )
        # All five top-level sections present.
        for key in ("wallet", "positions", "smoke_gate", "heartbeat", "recent_closes"):
            assert key in st, f"missing section {key}"
        # generated_at is set.
        assert st["generated_at"]

    def test_wallet_section_carries_equity(self, status_mod, fake_broker, fake_diagnose_mod):
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=[],
        )
        assert st["wallet"]["equity_usdt"] == pytest.approx(9921.51)
        # baseline_usdt may be None or a real number depending on
        # whether the test repo has data/wallet_baseline_usdt.txt;
        # accept either.
        assert "baseline_usdt" in st["wallet"]

    def test_positions_empty_when_flat(self, status_mod, fake_broker, fake_diagnose_mod):
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=[],
        )
        assert st["positions"] == []

    def test_positions_reports_open_position(self, status_mod, fake_broker, fake_diagnose_mod):
        fake_broker._client.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "contracts": 0.01,
                "entryPrice": 60000.0,
                "markPrice": 60100.0,
                "unrealizedPnl": 1.0,
                "info": {"positionAmt": "0.01"},
            },
            # Flat row — should be filtered out.
            {
                "symbol": "ETH/USDT:USDT",
                "side": "long",
                "contracts": 0,
                "entryPrice": 0.0,
                "info": {"positionAmt": "0"},
            },
        ]
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=[],
        )
        assert len(st["positions"]) == 1
        p = st["positions"][0]
        assert p["pair"] == "BTC/USDT:USDT"
        assert p["side"] == "BUY"
        assert p["contracts"] == pytest.approx(0.01)

    def test_smoke_gate_section_passes_through(self, status_mod, fake_broker, fake_diagnose_mod):
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=[],
        )
        # The classifier is mocked — assert we passed through its result.
        fake_diagnose_mod.build_smoke_gate.assert_called_once()
        assert st["smoke_gate"]["smoke_gate_pass"] is False
        assert "XRP/USDT:USDT" in st["smoke_gate"]["pairs_passed"]

    def test_recent_closes_filters_synthetic(self, status_mod, fake_broker, fake_diagnose_mod):
        """Only broker-truth rows (broker != paper, pnl_r set) make
        the list. Synthetic paper rows are filtered out."""
        rows = [
            # Broker-truth: included
            {
                "pair": "XRP/USDT:USDT",
                "outcome": "WIN",
                "broker": "binance-live",
                "pnl_r": 5.018,
                "closed_ts": "2026-06-06T04:37:30+00:00",
                "close_reason": "TP",
                "fees_paid": None,
            },
            # Synthetic paper: excluded
            {
                "pair": "BTC/USDT:USDT",
                "outcome": "LOSS",
                "broker": "paper",
                "pnl_r": None,
                "closed_ts": "2026-06-06T03:00:00+00:00",
                "close_reason": "SL",
                "fees_paid": None,
            },
            # Synthetic live-bug: excluded (pnl_r is None even though
            # broker is non-paper)
            {
                "pair": "ETH/USDT:USDT",
                "outcome": "WIN",
                "broker": "binance-live",
                "pnl_r": None,
                "closed_ts": "2026-06-06T02:00:00+00:00",
                "close_reason": "TP",
                "fees_paid": None,
            },
        ]
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=rows,
        )
        assert len(st["recent_closes"]) == 1
        assert st["recent_closes"][0]["pair"] == "XRP/USDT:USDT"
        assert st["recent_closes"][0]["pnl_r"] == pytest.approx(5.018)

    def test_recent_closes_newest_first(self, status_mod, fake_broker, fake_diagnose_mod):
        rows = [
            {
                "pair": "BTC/USDT:USDT",
                "outcome": "WIN",
                "broker": "binance-live",
                "pnl_r": 1.0,
                "closed_ts": "2026-06-06T01:00:00+00:00",
                "close_reason": "TP",
                "fees_paid": 0.01,
            },
            {
                "pair": "SOL/USDT:USDT",
                "outcome": "LOSS",
                "broker": "binance-live",
                "pnl_r": -1.0,
                "closed_ts": "2026-06-06T03:00:00+00:00",
                "close_reason": "SL",
                "fees_paid": 0.01,
            },
        ]
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=rows,
        )
        # SOL is later → first in the list.
        assert st["recent_closes"][0]["pair"] == "SOL/USDT:USDT"
        assert st["recent_closes"][1]["pair"] == "BTC/USDT:USDT"

    def test_recent_closes_caps_at_five(self, status_mod, fake_broker, fake_diagnose_mod):
        rows = [
            {
                "pair": "BTC/USDT:USDT",
                "outcome": "WIN",
                "broker": "binance-live",
                "pnl_r": float(i),
                "closed_ts": f"2026-06-06T{i:02d}:00:00+00:00",
                "close_reason": "TP",
                "fees_paid": 0.01,
            }
            for i in range(10)
        ]
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=rows,
        )
        assert len(st["recent_closes"]) == 5


class TestPrintHuman:
    """Pretty-printer must not crash on any of the common shapes."""

    def test_renders_flat_account(self, status_mod, fake_broker, fake_diagnose_mod, capsys):
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=[],
        )
        status_mod._print_human(st)
        out = capsys.readouterr().out
        # Section headers are visible.
        for header in (
            "[Wallet]",
            "[Open positions]",
            "[4-pair smoke gate]",
            "[Heartbeat]",
            "[Last 5 broker-truth closes]",
        ):
            assert header in out
        # Equity rendered.
        assert "$9921.51" in out
        # Empty positions message visible.
        assert "(none — all flat)" in out

    def test_renders_with_open_position(self, status_mod, fake_broker, fake_diagnose_mod, capsys):
        fake_broker._client.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT:USDT",
                "side": "short",
                "contracts": 0.005,
                "entryPrice": 60500.0,
                "markPrice": 60200.0,
                "unrealizedPnl": 1.5,
            },
        ]
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=[],
        )
        status_mod._print_human(st)
        out = capsys.readouterr().out
        assert "BTC/USDT:USDT" in out
        assert "SELL" in out
        assert "+$1.50" in out

    def test_renders_recent_closes(self, status_mod, fake_broker, fake_diagnose_mod, capsys):
        rows = [
            {
                "pair": "XRP/USDT:USDT",
                "outcome": "WIN",
                "broker": "binance-live",
                "pnl_r": 5.018,
                "closed_ts": "2026-06-06T04:37:30+00:00",
                "close_reason": "TP",
                "fees_paid": 0.04,
            }
        ]
        st = status_mod.build_status(
            broker=fake_broker,
            pairs=PAIRS,
            diagnose_mod=fake_diagnose_mod,
            journal_rows=rows,
        )
        status_mod._print_human(st)
        out = capsys.readouterr().out
        assert "XRP/USDT:USDT" in out
        assert "+5.018" in out
        assert "TP" in out
