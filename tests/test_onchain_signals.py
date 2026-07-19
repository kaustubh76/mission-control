"""Unit tests for the on-chain strategy signals (derived from the CMC onchain@* harvest)."""

from __future__ import annotations

from ictbot.strategy import onchain_signals as sig
from ictbot.data import cmc_stream_store

_METRIC = {
    "ts": 1, "price": 1.4,
    "windows": {"24h": {"buy_vol_usd": 3000000.0, "sell_vol_usd": 6000000.0,
                        "unique_traders": 607.0, "vol_usd": 9000000.0}},
}


def test_flow_ratio():
    assert sig.flow_ratio(_METRIC, "24h") == 3000000.0 / 9000000.0  # net selling (< 0.5)
    assert sig.flow_ratio({"windows": {"24h": {"buy_vol_usd": 8.0, "sell_vol_usd": 2.0}}}) == 0.8
    assert sig.flow_ratio({"windows": {}}, "24h") is None  # missing window
    assert sig.flow_ratio({"windows": {"24h": {"buy_vol_usd": 0, "sell_vol_usd": 0}}}) is None  # no vol


def test_token_signal_assembles_fields(monkeypatch):
    monkeypatch.setattr(cmc_stream_store, "onchain_token_metrics", lambda *a, **k: {"CAKE": _METRIC})
    monkeypatch.setattr(cmc_stream_store, "onchain_holders", lambda *a, **k: {"CAKE": {"top10_pct": 4.0, "top100_pct": 5.7}})
    monkeypatch.setattr(cmc_stream_store, "onchain_net_liquidity_usd", lambda *a, **k: -12500.0)
    s = sig.token_signal("CAKE")
    assert round(s["flow_ratio"], 3) == 0.333 and s["unique_traders"] == 607.0
    assert s["top10_pct"] == 4.0 and s["net_liquidity_usd"] == -12500.0


def test_token_signal_none_when_cold(monkeypatch):
    monkeypatch.setattr(cmc_stream_store, "onchain_token_metrics", lambda *a, **k: {})
    monkeypatch.setattr(cmc_stream_store, "onchain_holders", lambda *a, **k: {})
    monkeypatch.setattr(cmc_stream_store, "onchain_net_liquidity_usd", lambda *a, **k: None)
    assert sig.token_signal("CAKE") is None  # no fresh data anywhere → None


def test_onchain_signals_skips_cold_tokens(monkeypatch):
    monkeypatch.setattr(cmc_stream_store, "onchain_token_metrics", lambda *a, **k: {"CAKE": _METRIC})
    monkeypatch.setattr(cmc_stream_store, "onchain_holders", lambda *a, **k: {})
    monkeypatch.setattr(cmc_stream_store, "onchain_net_liquidity_usd", lambda *a, **k: None)
    out = sig.onchain_signals(["CAKE", "LINK"])
    assert "CAKE" in out and "LINK" not in out  # LINK had no data → omitted
