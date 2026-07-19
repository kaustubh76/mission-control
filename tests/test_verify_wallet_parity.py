"""
Tier 3 / Fix 5.F tests for scripts/verify_wallet_parity.py.

Pure-function tests for the journal computation + parity comparison.
End-to-end is exercised via the script's exit codes against synthetic
inputs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_wallet_parity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_wallet_parity", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V = _load_module()


# ---- compute_journal_pnl --------------------------------------------------


def _row(
    pair="BTC/USDT:USDT",
    outcome="WIN",
    pnl_r=2.0,
    fees=0.05,
    ts="2026-06-06T12:00:00+00:00",
    broker="binance-live",
):
    return {
        "pair": pair,
        "entry": "BUY",
        "outcome": outcome,
        "pnl_r": pnl_r,
        "fees_paid": fees,
        "closed_ts": ts,
        "ts": ts,
        "broker": broker,
        "price": 100.0,
        "sl": 95.0,
        "tp": 110.0,
    }


def test_journal_pnl_only_counts_binance_live_closed_with_pnl_r():
    rows = [
        _row(),  # OK
        _row(broker="paper"),  # wrong broker
        _row(outcome="OPEN"),  # not closed
        _row(pnl_r=None),  # broker-truth missing
    ]
    out = V.compute_journal_pnl(
        rows, since_iso="2026-06-06", risk_pct_live=0.0005, starting_balance=10_000.0
    )
    assert out["rows_counted"] == 1
    # 2.0 R × 0.0005 × 10000 = 10.0 USDT
    assert out["journal_usdt"] == 10.0
    assert out["fees_paid_total"] == 0.05


def test_journal_pnl_filters_by_since_cutoff():
    rows = [
        _row(ts="2026-06-05T12:00:00+00:00"),  # before cutoff
        _row(ts="2026-06-06T12:00:00+00:00"),  # after cutoff
    ]
    out = V.compute_journal_pnl(
        rows, since_iso="2026-06-06", risk_pct_live=0.0005, starting_balance=10_000.0
    )
    assert out["rows_counted"] == 1


def test_journal_pnl_per_pair_breakdown_aggregates():
    rows = [
        _row(pair="BTC/USDT:USDT", pnl_r=2.0, fees=0.05),
        _row(pair="BTC/USDT:USDT", pnl_r=-1.0, fees=0.04),
        _row(pair="ETH/USDT:USDT", pnl_r=3.0, fees=0.03),
    ]
    out = V.compute_journal_pnl(
        rows, since_iso="2026-06-06", risk_pct_live=0.0005, starting_balance=10_000.0
    )
    assert out["rows_counted"] == 3
    btc = out["per_pair"]["BTC/USDT:USDT"]
    assert btc["n"] == 2
    # (2.0 - 1.0) × 0.0005 × 10000 = 5.0
    assert btc["pnl_usdt"] == 5.0
    assert btc["fees"] == round(0.05 + 0.04, 4)
    eth = out["per_pair"]["ETH/USDT:USDT"]
    assert eth["n"] == 1
    assert eth["pnl_usdt"] == 15.0


def test_journal_pnl_zero_rows():
    out = V.compute_journal_pnl(
        [], since_iso="2026-06-06", risk_pct_live=0.0005, starting_balance=10_000.0
    )
    assert out["rows_counted"] == 0
    assert out["journal_usdt"] == 0.0
    assert out["per_pair"] == {}


# ---- compute_parity -------------------------------------------------------


def test_parity_ok_within_tolerance():
    out = V.compute_parity(journal_usdt=10.0, wallet_delta=10.3, tolerance=0.5)
    assert out["parity_ok"] is True
    assert out["drift_usdt"] == 0.3


def test_parity_fails_outside_tolerance():
    out = V.compute_parity(journal_usdt=10.0, wallet_delta=12.0, tolerance=0.5)
    assert out["parity_ok"] is False
    assert out["drift_usdt"] == 2.0


def test_parity_handles_negative_drift():
    """Wallet lost more than the journal recorded — e.g. unreported
    fees or off-strategy losses. Still fails the same way."""
    out = V.compute_parity(journal_usdt=10.0, wallet_delta=8.0, tolerance=0.5)
    assert out["parity_ok"] is False
    assert out["drift_usdt"] == -2.0


def test_parity_zero_journal_zero_wallet():
    """No closes, no movement — parity trivially holds."""
    out = V.compute_parity(journal_usdt=0.0, wallet_delta=0.0, tolerance=0.5)
    assert out["parity_ok"] is True


# ---- baseline file round-trip --------------------------------------------


def test_baseline_round_trip(tmp_path):
    """Round-trip preserves 6 decimal places — sufficient for USDT
    accounting (sub-microcent precision)."""
    import pytest

    path = tmp_path / "wallet_baseline.txt"
    V._write_baseline(path, 9_953.489212)
    assert V._read_baseline(path) == pytest.approx(9_953.489212, abs=1e-5)


def test_baseline_missing_returns_none(tmp_path):
    assert V._read_baseline(tmp_path / "missing.txt") is None


def test_baseline_corrupt_returns_none(tmp_path):
    path = tmp_path / "corrupt.txt"
    path.write_text("not a number\n")
    assert V._read_baseline(path) is None
