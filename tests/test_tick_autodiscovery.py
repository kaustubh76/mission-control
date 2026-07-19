"""
Auto-discover tick_size from exchange market metadata.

`BinanceExchange.tick_size(symbol)` lazy-loads markets via ccxt and
caches the precision so subsequent calls don't refetch. Failure to find
the symbol returns None so callers fall back to legacy rounding.

Also verifies `analyze_pair` actually forwards the discovered tick into
the strategy — without this the auto-discovery is just decoration.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from ictbot.data.binance import BinanceExchange
from ictbot.orchestrator import analyzer


def test_tick_size_reads_precision_from_load_markets():
    ex = BinanceExchange()
    ex._client = MagicMock()
    ex._client.load_markets.return_value = {
        "XRP/USDT:USDT": {"precision": {"price": 0.0001}},
        "BTC/USDT:USDT": {"precision": {"price": 0.5}},
    }
    assert ex.tick_size("XRP/USDT:USDT") == 0.0001
    assert ex.tick_size("BTC/USDT:USDT") == 0.5


def test_tick_size_caches_after_first_call():
    ex = BinanceExchange()
    ex._client = MagicMock()
    ex._client.load_markets.return_value = {"BTC/USDT:USDT": {"precision": {"price": 0.5}}}
    ex.tick_size("BTC/USDT:USDT")
    ex.tick_size("BTC/USDT:USDT")
    ex.tick_size("BTC/USDT:USDT")
    # load_markets called at most once for repeated queries on the same symbol.
    assert ex._client.load_markets.call_count == 1


def test_tick_size_returns_none_when_precision_missing():
    ex = BinanceExchange()
    ex._client = MagicMock()
    ex._client.load_markets.return_value = {"WEIRD/USDT:USDT": {}}
    assert ex.tick_size("WEIRD/USDT:USDT") is None


def test_tick_size_returns_none_when_load_markets_fails():
    """Exchange offline / rate limited → return None and don't blow up."""
    ex = BinanceExchange()
    ex._client = MagicMock()
    ex._client.load_markets.side_effect = RuntimeError("offline")
    assert ex.tick_size("BTC/USDT:USDT") is None


def test_tick_size_unknown_symbol_returns_none():
    ex = BinanceExchange()
    ex._client = MagicMock()
    ex._client.load_markets.return_value = {"BTC/USDT:USDT": {"precision": {"price": 0.5}}}
    assert ex.tick_size("UNLISTED/USDT:USDT") is None


def test_analyze_pair_forwards_auto_tick_to_evaluate_frames(monkeypatch):
    """The orchestrator passes the discovered tick to the strategy.
    Mock evaluate_frames to capture the kwarg."""

    def fake_get_data(symbol, tf, limit):
        return pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=300, freq="1min"),
                "open": [100] * 300,
                "high": [101] * 300,
                "low": [99] * 300,
                "close": [100] * 300,
                "volume": [10] * 300,
            }
        )

    captured = {}

    def fake_evaluate(*args, **kw):
        captured.update(kw)
        return {
            "error": None,
            "entry": "NO ENTRY",
            "pair": kw.get("pair") or args[5],
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
        }

    monkeypatch.setattr(analyzer, "get_data", fake_get_data)
    monkeypatch.setattr(analyzer, "evaluate_frames", fake_evaluate)
    monkeypatch.setattr(analyzer._default_exchange, "tick_size", lambda symbol: 0.0001)
    # Skip the journal settle (it touches disk).
    monkeypatch.setattr(analyzer, "settle_open_signals", lambda _: 0)

    analyzer.analyze_pair("XRP/USDT:USDT", notify=False)
    assert captured.get("tick_size") == 0.0001


def test_analyze_pair_handles_tick_lookup_failure_gracefully(monkeypatch):
    def fake_get_data(symbol, tf, limit):
        return pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=300, freq="1min"),
                "open": [100] * 300,
                "high": [101] * 300,
                "low": [99] * 300,
                "close": [100] * 300,
                "volume": [10] * 300,
            }
        )

    captured = {}

    def fake_evaluate(*args, **kw):
        captured.update(kw)
        return {
            "error": None,
            "entry": "NO ENTRY",
            "pair": kw.get("pair") or args[5],
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
        }

    monkeypatch.setattr(analyzer, "get_data", fake_get_data)
    monkeypatch.setattr(analyzer, "evaluate_frames", fake_evaluate)

    def raise_(symbol):
        raise RuntimeError("offline")

    monkeypatch.setattr(analyzer._default_exchange, "tick_size", raise_)
    monkeypatch.setattr(analyzer, "settle_open_signals", lambda _: 0)

    # Should NOT raise — analyzer must tolerate the lookup failure.
    analyzer.analyze_pair("XRP/USDT:USDT", notify=False)
    assert captured.get("tick_size") is None
