"""FREE, keyless data adapters (the CoinMarketCap replacement) — offline, deterministic.

Covers: freefeeds (Binance klines/ticker, alternative.me F&G) parsing + never-raise; dexscreener
signal parsing + best-pair selection; and that the CMC-named seams (fear_greed, cmc_price,
daily_ohlcv) route to the free sources under FREE_DATA. All network is stubbed — no live calls."""

from __future__ import annotations

import pandas as pd
import pytest

from ictbot.data import cmc, dexscreener, freefeeds
from ictbot.data import cmc_intel


class _Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


@pytest.fixture(autouse=True)
def _clear_caches():
    freefeeds._cache.clear()
    dexscreener._cache.clear()
    yield
    freefeeds._cache.clear()
    dexscreener._cache.clear()


# --------------------------- freefeeds: alt.me F&G ------------------------ #
def test_alt_me_fear_greed_parses_string_value(monkeypatch):
    payload = {"data": [{"value": "63", "value_classification": "Greed", "timestamp": "1"}]}
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp(payload))
    assert freefeeds.alt_me_fear_greed() == 63  # string "63" -> int 63
    assert freefeeds.alt_me_fng_classification() == "Greed"


def test_alt_me_fear_greed_never_raises_on_failure(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr("requests.get", _boom)
    assert freefeeds.alt_me_fear_greed() is None  # degrades, no raise


# --------------------------- freefeeds: Binance --------------------------- #
def test_binance_klines_frame_shape(monkeypatch):
    row = [1700000000000, "10.0", "12.0", "9.0", "11.0", "100.0", 1700000000001, "0", 5, "0", "0", "0"]
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp([row, row]))
    df = freefeeds.binance_klines("BNB", "4h", 2)
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 2 and df["close"].iloc[-1] == 11.0


def test_binance_ticker_price(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp({"price": "568.4"}))
    assert freefeeds.binance_ticker_price("BNB") == 568.4
    assert freefeeds.binance_ticker_price("USDT") == 1.0  # short-circuit, no call


# --------------------------- dexscreener --------------------------------- #
def _dex_pair(addr, price, liq, vol=1000.0, chg=1.5, buys=3, sells=2):
    return {
        "chainId": "bsc",
        "baseToken": {"address": addr},
        "priceUsd": str(price),
        "liquidity": {"usd": liq},
        "volume": {"h24": vol},
        "priceChange": {"h24": chg},
        "txns": {"h24": {"buys": buys, "sells": sells}},
    }


def test_dex_signals_picks_deepest_liquidity_pair(monkeypatch):
    bnb = dexscreener.BSC_TOKEN_ADDRS["BNB"]
    shallow = _dex_pair(bnb, 500.0, 1_000.0)
    deep = _dex_pair(bnb, 568.0, 9_000_000.0, buys=100, sells=58)
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp([shallow, deep]))
    sig = dexscreener.dex_signals(["BNB"])["BNB"]
    assert sig["price_usd"] == 568.0  # from the deepest-liquidity pair
    assert sig["liquidity_usd"] == 9_000_000.0
    assert sig["txns_24h"] == 158


def test_dex_signals_never_raises(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    assert dexscreener.dex_signals(["BNB"]) == {}  # empty, no raise


# --------------------------- seam routing under FREE_DATA ----------------- #
def test_fear_greed_routes_to_alt_me_when_free(monkeypatch):
    monkeypatch.setattr(cmc.settings, "free_data", True)
    monkeypatch.setattr(cmc.settings, "cmc_only", False)
    monkeypatch.setattr(freefeeds, "alt_me_fear_greed", lambda: 41)
    # CMC.get must NOT be reached when the free source answers.
    monkeypatch.setattr(cmc.CMC, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("CMC hit")))
    assert cmc.fear_greed() == 41


def test_cmc_price_routes_to_binance_when_free(monkeypatch):
    monkeypatch.setattr(cmc.settings, "free_data", True)
    monkeypatch.setattr(cmc.settings, "cmc_only", False)
    monkeypatch.setattr(freefeeds, "binance_ticker_price", lambda s: 568.4)
    monkeypatch.setattr(cmc.CMC, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("CMC hit")))
    assert cmc.cmc_price("BNB") == 568.4


def test_daily_ohlcv_routes_to_binance_when_free(monkeypatch):
    monkeypatch.setattr(cmc_intel.settings, "free_data", True)
    monkeypatch.setattr(cmc_intel.settings, "cmc_only", False)
    frame = pd.DataFrame(
        {"time": [pd.Timestamp("2026-01-01")], "open": [1.0], "high": [1.0], "low": [1.0],
         "close": [2.0], "volume": [0.0]}
    )
    monkeypatch.setattr(freefeeds, "binance_klines", lambda sym, interval="1d", limit=1000: frame)
    out = cmc_intel.daily_ohlcv("BNB", days=5)
    assert out is not None and out["close"].iloc[-1] == 2.0


# --------------------------- on-chain signals (DexScreener) --------------- #
def test_token_signals_free_branch_maps_dexscreener(monkeypatch):
    from ictbot.strategy import market_signals

    monkeypatch.setattr(market_signals.settings, "free_data", True)
    monkeypatch.setattr(
        "ictbot.data.dexscreener.dex_signals",
        lambda syms: {
            "BNB": {"price_change_h24": 1.2, "volume_24h": 5.0, "liquidity_usd": 9.0,
                    "market_cap": 100.0, "buys_24h": 60, "sells_24h": 40, "txns_24h": 100}
        },
    )
    sig = market_signals.token_signals(["BNB", "ETH"])
    assert "ETH" not in sig  # no dex data -> omitted, no crash
    b = sig["BNB"]
    assert b["pct_24h"] == 1.2 and b["volume_24h"] == 5.0 and b["liquidity_usd"] == 9.0
    assert b["market_cap"] == 100.0
    assert b["flow_ratio"] == 0.6  # 60 / (60 + 40)
    # holder/whale fields have no free source -> None, which every overlay tolerates
    assert b["top10_pct"] is None and b["whale_net_usd"] is None and b["unique_traders"] is None


# --------------------------- free market_overview ------------------------- #
def test_coingecko_global_parses_btc_dominance(monkeypatch):
    payload = {"data": {"market_cap_percentage": {"btc": 52.3},
                        "market_cap_change_percentage_24h_usd": -1.2}}
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp(payload))
    g = freefeeds.coingecko_global()
    assert g["btc_dominance"] == 52.3 and g["mktcap_change_24h"] == -1.2


def test_free_market_overview_keeps_cmc_field_names(monkeypatch):
    from ictbot.data import free_overview

    monkeypatch.setattr("ictbot.data.freefeeds.alt_me_fear_greed", lambda: 25)
    monkeypatch.setattr("ictbot.data.freefeeds.coingecko_global",
                        lambda: {"btc_dominance": 52.0, "mktcap_change_24h": -1.0})
    monkeypatch.setattr("ictbot.data.dexscreener.dex_signals",
                        lambda syms: {"BNB": {"price_change_h24": 2.0}, "ETH": {"price_change_h24": -1.0}})
    monkeypatch.setattr(free_overview, "free_ta_health", lambda: 0.5)
    ov = free_overview.free_market_overview()
    for k in ("skill_source", "risk_budget", "regime", "fear_greed", "btc_dominance",
              "mktcap_change_24h", "headline", "narratives", "tools_used"):
        assert k in ov, f"missing {k}"
    assert ov["fear_greed"] == 25 and ov["btc_dominance"] == 52.0
    assert ov["regime"] in ("risk-on", "neutral", "risk-off")
    assert ov["narratives"][0] == "BNB"  # top 24h mover


# --------------------------- multi-window returns (7d/30d) ---------------- #
def test_window_returns_computes_7d_30d(monkeypatch):
    closes = list(range(1, 36))  # 35 daily closes 1..35
    df = pd.DataFrame({"close": [float(c) for c in closes]})
    monkeypatch.setattr(freefeeds, "binance_klines", lambda sym, interval="1d", limit=35: df)
    w = freefeeds.window_returns("BNB")
    assert w["pct_7d"] == round((35 / 28 - 1) * 100, 4)   # close[-8] = 28
    assert w["pct_30d"] == round((35 / 5 - 1) * 100, 4)    # close[-31] = 5


def test_window_returns_short_history_is_none(monkeypatch):
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})  # < 8 rows
    monkeypatch.setattr(freefeeds, "binance_klines", lambda sym, interval="1d", limit=35: df)
    assert freefeeds.window_returns("BNB") == {"pct_7d": None, "pct_30d": None}


def test_dex_token_signals_fills_windows(monkeypatch):
    from ictbot.strategy import market_signals

    monkeypatch.setattr(market_signals.settings, "free_data", True)
    monkeypatch.setattr("ictbot.data.dexscreener.dex_signals",
                        lambda syms: {"BNB": {"price_change_h24": 1.0, "buys_24h": 1, "sells_24h": 1}})
    monkeypatch.setattr("ictbot.data.freefeeds.window_returns",
                        lambda s: {"pct_7d": 5.0, "pct_30d": 10.0})
    b = market_signals.token_signals(["BNB"])["BNB"]
    assert b["pct_24h"] == 1.0 and b["pct_7d"] == 5.0 and b["pct_30d"] == 10.0
    # mom_blend now has all three windows
    assert market_signals.mom_blend(b) is not None


def test_dex_signals_with_windows_flag(monkeypatch):
    payload = [_dex_pair(dexscreener.BSC_TOKEN_ADDRS["BNB"], 568.0, 9_000_000.0)]
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp(payload))
    monkeypatch.setattr("ictbot.data.freefeeds.window_returns", lambda s: {"pct_7d": 3.3, "pct_30d": 8.0})
    with_w = dexscreener.dex_signals(["BNB"], with_windows=True)["BNB"]
    assert with_w["pct_7d"] == 3.3 and with_w["pct_30d"] == 8.0
    dexscreener._cache.clear()
    without = dexscreener.dex_signals(["BNB"])["BNB"]
    assert "pct_7d" not in without  # default omits (hot path stays cheap)


def test_free_market_overview_never_raises_offline(monkeypatch):
    from ictbot.data import free_overview

    monkeypatch.setattr("ictbot.data.freefeeds.alt_me_fear_greed", lambda: None)
    monkeypatch.setattr("ictbot.data.freefeeds.coingecko_global", lambda: None)
    monkeypatch.setattr("ictbot.data.dexscreener.dex_signals", lambda syms: {})
    monkeypatch.setattr(free_overview, "free_ta_health", lambda: None)
    assert free_overview.free_market_overview() is None  # nothing resolved -> None, no raise
