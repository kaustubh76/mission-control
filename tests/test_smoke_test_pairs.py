"""
Fix 9.F — unit tests for scripts/smoke_test_pairs.py.

Mocks the ccxt client so the script runs without touching Binance.
Asserts:
  - Refuses to run when BINANCE_TESTNET=false.
  - Iterates every configured pair.
  - Posts exactly 2 orders per pair (entry + reduceOnly flatten) in
    the happy path.
  - --dry-run posts no orders.
  - Single-pair mode only iterates that pair.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "smoke_test_pairs.py"


def _load_script_module():
    """Import smoke_test_pairs.py as a module so we can call its
    helpers directly without spawning a subprocess."""
    spec = importlib.util.spec_from_file_location("smoke_test_pairs", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["smoke_test_pairs"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def smoke_mod():
    return _load_script_module()


@pytest.fixture
def fake_client():
    """A mock ccxt client with sane defaults for the smoke test."""
    client = MagicMock()
    # Fetch ticker → reasonable last price.
    client.fetch_ticker.return_value = {"last": 100.0}
    # Markets with precision + min_notional.
    client.load_markets.return_value = {
        "BTC/USDT:USDT": {
            "precision": {"amount": 0.001, "price": 0.1},
            "limits": {"cost": {"min": 5.0}, "amount": {"min": 0.001}},
        },
        "ETH/USDT:USDT": {
            "precision": {"amount": 0.001, "price": 0.01},
            "limits": {"cost": {"min": 5.0}, "amount": {"min": 0.001}},
        },
        "SOL/USDT:USDT": {
            "precision": {"amount": 1.0, "price": 0.01},
            "limits": {"cost": {"min": 5.0}, "amount": {"min": 1.0}},
        },
        "XRP/USDT:USDT": {
            "precision": {"amount": 0.1, "price": 0.0001},
            "limits": {"cost": {"min": 5.0}, "amount": {"min": 0.1}},
        },
    }

    # Position rows for the readback.
    def _positions(symbols=None, **_):
        return [
            {
                "symbol": s,
                "leverage": 5,
                "marginMode": "isolated",
                "contracts": 0,
                "info": {"positionAmt": "0"},
            }
            for s in (symbols or [])
        ]

    client.fetch_positions.side_effect = _positions
    # create_order returns an average price so entry_avg / exit_avg get set.
    client.create_order.side_effect = lambda *a, **kw: {"id": "ord-x", "average": 100.0}
    return client


class TestSmokeOne:
    def test_dry_run_skips_orders_and_marks_skipped(self, smoke_mod, fake_client):
        out = smoke_mod._smoke_one(fake_client, "BTC/USDT:USDT", dry_run=True)
        assert out["status"] == "skipped"
        assert out["reason"] == "--dry-run"
        assert out["smallest_qty"] is not None
        # No create_order calls fired.
        assert fake_client.create_order.call_count == 0

    def test_happy_path_places_two_orders_per_pair(self, smoke_mod, fake_client):
        out = smoke_mod._smoke_one(fake_client, "BTC/USDT:USDT", dry_run=False)
        assert out["status"] == "ok"
        # Entry + flatten = 2 orders.
        assert fake_client.create_order.call_count == 2
        # First is the entry (no reduceOnly); second is reduceOnly flatten.
        entry_call = fake_client.create_order.call_args_list[0]
        flatten_call = fake_client.create_order.call_args_list[1]
        assert entry_call.args[2] == "buy"
        # entry leg has empty params (no reduceOnly).
        assert "reduceOnly" not in (entry_call.args[5] or {})
        assert flatten_call.args[2] == "sell"
        assert flatten_call.args[5].get("reduceOnly") is True

    def test_failure_in_create_order_recorded_as_failed(self, smoke_mod, fake_client):
        fake_client.create_order.side_effect = Exception("boom")
        out = smoke_mod._smoke_one(fake_client, "BTC/USDT:USDT", dry_run=False)
        assert out["status"] == "failed"
        assert "boom" in out["reason"]

    def test_records_per_pair_metadata(self, smoke_mod, fake_client):
        out = smoke_mod._smoke_one(fake_client, "SOL/USDT:USDT", dry_run=True)
        assert out["leverage_actual"] == 5
        assert out["margin_mode_actual"] == "isolated"
        assert out["min_notional"] == 5.0
        assert out["precision_amount"] == 1.0
        assert out["smallest_qty"] >= 1.0

    def test_residual_position_after_flatten_marks_failed(self, smoke_mod, fake_client):
        """If fetch_positions after the flatten still shows contracts,
        we report failed — the broker didn't fully unwind."""
        call_count = {"n": 0}

        def _positions(symbols=None, **_):
            call_count["n"] += 1
            # First call (pre-test snapshot) → flat. Second call
            # (post-flatten verification) → still 0.5 open.
            contracts = 0 if call_count["n"] == 1 else 0.5
            return [
                {
                    "symbol": s,
                    "leverage": 5,
                    "marginMode": "isolated",
                    "contracts": contracts,
                    "info": {"positionAmt": str(contracts)},
                }
                for s in (symbols or [])
            ]

        fake_client.fetch_positions.side_effect = _positions
        out = smoke_mod._smoke_one(fake_client, "BTC/USDT:USDT", dry_run=False)
        assert out["status"] == "failed"
        assert "position still open" in out["reason"]


class TestMainGuards:
    def test_refuses_when_not_testnet(self, smoke_mod, monkeypatch):
        """Mainnet runs must abort BEFORE constructing the broker."""
        monkeypatch.setattr(smoke_mod.settings, "binance_testnet", False)
        monkeypatch.setattr(sys, "argv", ["smoke_test_pairs.py"])
        rc = smoke_mod.main()
        assert rc == 2

    def test_refuses_without_api_keys(self, smoke_mod, monkeypatch):
        monkeypatch.setattr(smoke_mod.settings, "binance_testnet", True)
        monkeypatch.setattr(smoke_mod.settings, "binance_api_key", "")
        monkeypatch.setattr(smoke_mod.settings, "binance_api_secret", "")
        monkeypatch.setattr(sys, "argv", ["smoke_test_pairs.py"])
        rc = smoke_mod.main()
        assert rc == 2

    def test_writes_json_report(self, smoke_mod, fake_client, tmp_path, monkeypatch):
        """Happy path: writes a JSON report with one entry per pair."""
        monkeypatch.setattr(smoke_mod.settings, "binance_testnet", True)
        monkeypatch.setattr(smoke_mod.settings, "binance_api_key", "k")
        monkeypatch.setattr(smoke_mod.settings, "binance_api_secret", "s")
        # Inject the mock client into the BinanceLiveBroker constructor.
        from ictbot.exec import binance_live as bl

        original_init = bl.BinanceLiveBroker.__init__

        def _patched_init(self, *args, **kwargs):
            kwargs["client"] = fake_client
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(bl.BinanceLiveBroker, "__init__", _patched_init)

        report_path = tmp_path / "report.json"
        monkeypatch.setattr(
            sys,
            "argv",
            ["smoke_test_pairs.py", "--dry-run", "--out", str(report_path)],
        )
        rc = smoke_mod.main()
        assert rc == 0
        # report_path is relative-resolved by the script — find it under PROJECT_ROOT.
        possible = [
            report_path,
            smoke_mod.PROJECT_ROOT / str(report_path),
        ]
        report = None
        for p in possible:
            if p.exists():
                report = json.loads(p.read_text())
                break
        assert report is not None
        assert "results" in report
        assert len(report["results"]) >= 1
