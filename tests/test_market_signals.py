"""Tests for the CMC signal overlays (universe_overlay) + the unified signal buffet (market_signals)."""

from __future__ import annotations

from ictbot.strategy import universe_overlay as ov
from ictbot.strategy import market_signals
from ictbot.data import cmc_stream_store

_W = {"BNB": 0.4, "ETH": 0.4, "DOGE": 0.2}  # total = 1.0 (the deploy cap)


# --- liquidity floor -------------------------------------------------------- #
def test_liquidity_floor_drops_illiquid_keeps_total():
    sig = {"BNB": {"volume_24h": 5e8}, "ETH": {"volume_24h": 1e9}, "DOGE": {"volume_24h": 1e5}}
    out = ov.liquidity_floor(_W, sig, min_vol_usd=1e6)
    assert "DOGE" not in out and abs(sum(out.values()) - 1.0) < 1e-9  # dropped, cap preserved
    assert ov.liquidity_floor(_W, sig, 0) == _W  # no-op at 0


def test_liquidity_floor_keeps_unknown_volume():
    sig = {"BNB": {}, "ETH": {"volume_24h": 1e9}, "DOGE": {"volume_24h": 1e9}}
    out = ov.liquidity_floor(_W, sig, min_vol_usd=1e6)
    assert set(out) == {"BNB", "ETH", "DOGE"}  # unknown volume kept (don't over-filter)


# --- flow tilt -------------------------------------------------------------- #
def test_flow_tilt_buys_up_sells_down_same_total():
    sig = {"BNB": {"flow_ratio": 0.9}, "ETH": {"flow_ratio": 0.1}, "DOGE": {"flow_ratio": 0.5}}
    out = ov.flow_tilt(_W, sig, w=0.15)
    assert out["BNB"] > _W["BNB"] and out["ETH"] < _W["ETH"]  # net-buyer up, net-seller down
    assert abs(sum(out.values()) - 1.0) < 1e-9                 # deployment preserved
    assert ov.flow_tilt(_W, sig, w=0.0) == _W                  # no-op at 0


def test_flow_tilt_clamped_band():
    sig = {"BNB": {"flow_ratio": 1.0}, "ETH": {"flow_ratio": 0.0}}
    w = {"BNB": 0.5, "ETH": 0.5}
    out = ov.flow_tilt(w, sig, w=1.0, lo=0.85, hi=1.15)  # extreme flow, big w → clamp binds
    # pre-renorm multipliers clamp to [0.85,1.15]; ratio bounded
    assert out["BNB"] / out["ETH"] <= (1.15 / 0.85) + 1e-9


# --- concentration penalty -------------------------------------------------- #
def test_concentration_penalty():
    sig = {"BNB": {"top10_pct": 80.0}, "ETH": {"top10_pct": 10.0}, "DOGE": {"top10_pct": 5.0}}
    out = ov.concentration_penalty(_W, sig, max_top10_pct=50.0)
    assert out["BNB"] < _W["BNB"]  # over-concentrated → down-weighted
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert ov.concentration_penalty(_W, sig, 0) == _W  # no-op


# --- cap brake -------------------------------------------------------------- #
def test_liquidity_cap_brake_only_lowers():
    outflow = {"BNB": {"net_liquidity_usd": -100000.0, "whale_net_usd": -50000.0}}
    assert ov.liquidity_cap_brake({"BNB": 1.0}, outflow, liq_brake=0.5) < 1.0  # outflow → haircut
    inflow = {"BNB": {"net_liquidity_usd": 100000.0, "whale_net_usd": 50000.0}}
    assert ov.liquidity_cap_brake({"BNB": 1.0}, inflow, liq_brake=0.5) == 1.0  # inflow → no brake
    assert ov.liquidity_cap_brake({"BNB": 1.0}, outflow, liq_brake=0.0) == 1.0  # disabled


# --- unified buffet --------------------------------------------------------- #
def test_token_signals_merges_sources(monkeypatch):
    monkeypatch.setattr(cmc_stream_store, "quote_snapshot", lambda *a, **k: {"BNB": {"pct_7d": 3.4, "volume_24h": 1e9, "pct_24h": 1.0, "pct_30d": -2.0, "market_cap": 9e10}})
    monkeypatch.setattr(cmc_stream_store, "onchain_token_metrics", lambda *a, **k: {"BNB": {"windows": {"24h": {"buy_vol_usd": 6.0, "sell_vol_usd": 4.0, "unique_traders": 500}}}})
    monkeypatch.setattr(cmc_stream_store, "onchain_holders", lambda *a, **k: {"BNB": {"top10_pct": 12.0, "top100_pct": 30.0}})
    monkeypatch.setattr(cmc_stream_store, "onchain_token_liquidity", lambda *a, **k: {"BNB": {"liquidity_usd": 5e6}})
    monkeypatch.setattr(cmc_stream_store, "onchain_whale_flow", lambda *a, **k: {"BNB": {"whale_net_usd": -20000.0}})
    monkeypatch.setattr(cmc_stream_store, "onchain_net_liquidity_usd", lambda s, *a, **k: -5000.0 if s == "BNB" else None)
    s = market_signals.token_signals(["BNB", "ETH"])
    assert "ETH" not in s  # no data → omitted
    b = s["BNB"]
    assert b["pct_7d"] == 3.4 and round(b["flow_ratio"], 2) == 0.6 and b["liquidity_usd"] == 5e6
    assert b["top10_pct"] == 12.0 and b["whale_net_usd"] == -20000.0
    assert round(market_signals.mom_blend(b), 4) is not None  # multi-window blend computes
