"""
Unit tests for the CMC market-intelligence fetchers (Phase 2).

Hermetic: `CMC.get` is stubbed with captured-shape fixtures, the intel flag + capability
map are monkeypatched. Asserts (a) the parsers match the REAL response shapes, and
(b) the gating short-circuits never hit the network when intel is off / an endpoint is
marked unavailable.
"""

from __future__ import annotations

import pytest

from ictbot.data import cmc_intel, cmc_stream_store

# Captured-shape fixtures keyed by endpoint path.
_BODIES = {
    "/v1/global-metrics/quotes/latest": {
        "status": {"error_code": 0},
        "data": {
            "btc_dominance": 58.1,
            "eth_dominance": 9.26,
            "stablecoin_market_cap": 1e11,
            "quote": {
                "USD": {
                    "total_market_cap": 2.1e12,
                    "total_volume_24h": 8.3e10,
                    "altcoin_market_cap": 9e11,
                }
            },
        },
    },
    "/v1/global-metrics/quotes/historical": {
        "status": {"error_code": 0},
        "data": {
            "quotes": [
                {
                    "timestamp": "2026-05-11T00:00:00.000Z",
                    "btc_dominance": 60.0,
                    "quote": {"USD": {"total_market_cap": 2.7e12}},
                },
                {
                    "timestamp": "2026-06-10T00:00:00.000Z",
                    "btc_dominance": 58.1,
                    "quote": {"USD": {"total_market_cap": 2.1e12}},
                },
            ]
        },
    },
    "/v3/fear-and-greed/historical": {
        "status": {"error_code": "0"},  # v3 returns the STRING "0"
        "data": [
            {"timestamp": "1781049600", "value": 20, "value_classification": "Fear"},
            {"timestamp": "1780963200", "value": 14, "value_classification": "Extreme fear"},
        ],
    },
    "/v2/cryptocurrency/quotes/latest": {
        "status": {"error_code": 0},
        "data": {
            "BNB": [{"quote": {"USD": {"percent_change_24h": -2.5, "percent_change_7d": -9.1}}}],
            "ETH": [{"quote": {"USD": {"percent_change_24h": 1.2, "percent_change_7d": 3.4}}}],
        },
    },
    "/v1/cryptocurrency/trending/gainers-losers": {
        "status": {"error_code": 0},
        "data": [
            {"symbol": "AAA", "name": "A", "quote": {"USD": {"percent_change_24h": 50.0}}},
            {"symbol": "BBB", "name": "B", "quote": {"USD": {"percent_change_24h": -30.0}}},
        ],
    },
    "/v1/cryptocurrency/listings/latest": {
        "status": {"error_code": 0},
        "data": [
            {"symbol": "BTC", "name": "Bitcoin", "quote": {"USD": {"percent_change_24h": 2.1}}},
            {"symbol": "ETH", "name": "Ethereum", "quote": {"USD": {"percent_change_24h": -1.5}}},
            {"symbol": "SOL", "name": "Solana", "quote": {"USD": {"percent_change_24h": 5.0}}},
            {"symbol": "ADA", "name": "Cardano", "quote": {"USD": {"percent_change_24h": -3.0}}},
            {"symbol": "USDT", "name": "Tether", "quote": {"USD": {"percent_change_24h": None}}},
        ],
    },
    "/v1/cryptocurrency/categories": {
        "status": {"error_code": 0},
        "data": [
            {
                "name": "DeFi",
                "avg_price_change": 2.5,
                "market_cap": 1e9,
                "market_cap_change": 1.0,
                "num_tokens": 100,
            },
            {
                "name": "Memes",
                "avg_price_change": -1.0,
                "market_cap": 5e8,
                "market_cap_change": -2.0,
                "num_tokens": 50,
            },
        ],
    },
    "/v2/cryptocurrency/ohlcv/historical": {
        "status": {"error_code": "0"},
        "data": {
            "BNB": {
                "quotes": [
                    {
                        "time_open": "2026-06-07T00:00:00.000Z",
                        "quote": {
                            "USD": {
                                "open": 574.5,
                                "high": 606.8,
                                "low": 573.8,
                                "close": 603.6,
                                "volume": 1.3e9,
                            }
                        },
                    },
                    {
                        "time_open": "2026-06-08T00:00:00.000Z",
                        "quote": {
                            "USD": {
                                "open": 603.6,
                                "high": 610.0,
                                "low": 600.0,
                                "close": 605.0,
                                "volume": 1.1e9,
                            }
                        },
                    },
                ]
            }
        },
    },
}


@pytest.fixture
def intel_on(monkeypatch):
    """Enable intel + all-available capability map + stubbed CMC.get. Also neutralizes the
    CMC-WS quote snapshot (empty) so the REST-path parser tests are deterministic regardless of
    any local stream cache — the snapshot read-through is exercised by its own tests below."""
    monkeypatch.setattr(cmc_intel.settings, "cmc_intel_enabled", True)
    # These tests validate the legacy CMC REST parsers (via the stubbed CMC.get below), so pin the
    # CMC path: with FREE_DATA on (the default), daily_ohlcv would route to live Binance klines
    # instead of the mocked CMC body. Free-data adapters have their own tests (test_free_data.py).
    monkeypatch.setattr(cmc_intel.settings, "free_data", False)
    monkeypatch.setattr(cmc_intel, "_capability", lambda: {})  # nothing marked unavailable
    monkeypatch.setattr(cmc_stream_store, "quote_snapshot", lambda *a, **k: {})

    def fake_get(path, params=None, **kw):
        return _BODIES.get(path)

    monkeypatch.setattr(cmc_intel.CMC, "get", fake_get)


def _boom(*a, **k):
    raise AssertionError("network must not be called")


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #
def test_disabled_short_circuits_without_network(monkeypatch):
    monkeypatch.setattr(cmc_intel.settings, "cmc_intel_enabled", False)
    monkeypatch.setattr(cmc_intel.CMC, "get", _boom)
    assert cmc_intel.global_metrics() is None
    assert cmc_intel.fng_history() == []
    assert cmc_intel.build_regime_intel() is None
    assert cmc_intel.market_intel_snapshot() is None


def test_capability_unavailable_short_circuits(monkeypatch):
    monkeypatch.setattr(cmc_intel.settings, "cmc_intel_enabled", True)
    monkeypatch.setattr(
        cmc_intel, "_capability", lambda: {"/v1/global-metrics/quotes/latest": {"ok": False}}
    )
    monkeypatch.setattr(cmc_intel.CMC, "get", _boom)
    assert cmc_intel.global_metrics() is None  # marked unavailable → no call


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def test_global_metrics_parse(intel_on):
    g = cmc_intel.global_metrics()
    assert g["btc_dominance"] == 58.1 and g["eth_dominance"] == 9.26
    assert g["total_market_cap"] == 2.1e12 and g["altcoin_market_cap"] == 9e11


def test_fng_history_sorted_ascending(intel_on):
    h = cmc_intel.fng_history(14)
    assert [r["value"] for r in h] == [14, 20]  # sorted oldest-first by ts
    assert h[0]["label"] == "Extreme fear"


def test_token_changes_parse(intel_on):
    tc = cmc_intel.token_changes(["BNB", "ETH", "DOGE"])
    assert tc["BNB"]["pct_7d"] == -9.1 and tc["ETH"]["pct_24h"] == 1.2
    assert "DOGE" not in tc  # absent symbol omitted, no crash


def test_token_changes_prefers_fresh_snapshot(monkeypatch):
    """When ≥2 tokens carry a fresh pct_7d in the WS snapshot, token_changes serves them with
    ZERO network — the credit-saving read-through."""
    monkeypatch.setattr(cmc_intel.settings, "cmc_intel_enabled", True)
    monkeypatch.setattr(
        cmc_stream_store,
        "quote_snapshot",
        lambda *a, **k: {
            "BNB": {"pct_24h": 1.8, "pct_7d": 3.4},
            "ETH": {"pct_24h": -0.5, "pct_7d": 2.1},
        },
    )
    monkeypatch.setattr(cmc_intel.CMC, "get", _boom)  # network must NOT be called
    tc = cmc_intel.token_changes(["BNB", "ETH", "DOGE"])
    assert tc["BNB"]["pct_7d"] == 3.4 and tc["ETH"]["pct_24h"] == -0.5
    assert "DOGE" not in tc  # not in the snapshot → omitted, no crash


def test_token_changes_falls_back_when_snapshot_thin(intel_on, monkeypatch):
    """A snapshot with <2 fresh pct_7d tokens falls through to the REST quotes/latest path."""
    monkeypatch.setattr(
        cmc_stream_store, "quote_snapshot", lambda *a, **k: {"BNB": {"pct_24h": 1.8, "pct_7d": 3.4}}
    )  # only 1 token → not enough for the tilt → REST fallback
    tc = cmc_intel.token_changes(["BNB", "ETH", "DOGE"])
    assert tc["BNB"]["pct_7d"] == -9.1 and tc["ETH"]["pct_24h"] == 1.2  # REST fixture values


def test_gainers_losers_parse(intel_on):
    gl = cmc_intel.gainers_losers()
    assert gl["gainers"][0]["symbol"] == "AAA" and gl["gainers"][0]["pct_24h"] == 50.0
    assert gl["losers"][0]["symbol"] == "BBB"


def test_market_movers_sign_correct_top_mcap(intel_on):
    """movers come from listings/latest, split STRICTLY by sign (no positive 'losers')."""
    mv = cmc_intel.market_movers(100, 5)
    # gainers all > 0, sorted desc; SOL (+5) leads BTC (+2.1)
    assert [g["symbol"] for g in mv["gainers"]] == ["SOL", "BTC"]
    assert all(g["pct_24h"] > 0 for g in mv["gainers"])
    # losers all < 0, sorted asc (most negative first): ADA (-3) before ETH (-1.5)
    assert [l["symbol"] for l in mv["losers"]] == ["ADA", "ETH"]
    assert all(l["pct_24h"] < 0 for l in mv["losers"])
    # null-pct rows (e.g. a flat stablecoin) are dropped, not shown
    assert all(m["symbol"] != "USDT" for m in mv["gainers"] + mv["losers"])


def test_market_movers_falls_back_to_trending(monkeypatch, intel_on):
    """If listings/latest is unavailable, fall back to the (sign-corrected) trending source."""
    orig = cmc_intel.CMC.get
    monkeypatch.setattr(
        cmc_intel.CMC,
        "get",
        lambda path, params=None, **kw: None if "listings" in path else orig(path, params, **kw),
    )
    mv = cmc_intel.market_movers(100, 5)
    assert mv["gainers"][0]["symbol"] == "AAA" and mv["losers"][0]["symbol"] == "BBB"


def test_categories_sorted(intel_on):
    cats = cmc_intel.categories()
    assert [c["name"] for c in cats] == ["DeFi", "Memes"]  # by avg_price_change desc


def test_daily_ohlcv_frame(intel_on):
    df = cmc_intel.daily_ohlcv("BNB", days=5)
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 2 and df["close"].iloc[-1] == 605.0


def test_global_metrics_history_parse_and_order(intel_on):
    rows = cmc_intel.global_metrics_history(760)
    assert len(rows) == 2
    # oldest-first: the 2026-05-11 point (dom 60) before the 2026-06-10 point (dom 58.1)
    assert rows[0]["btc_dominance"] == 60.0 and rows[-1]["btc_dominance"] == 58.1
    assert rows[0]["total_market_cap"] == 2.7e12


def test_build_regime_intel_combines_sources(intel_on):
    ri = cmc_intel.build_regime_intel()
    assert ri["btc_dominance"] == 58.1 and ri["btc_dominance_prev"] == 60.0
    assert ri["total_mktcap"] == 2.1e12 and ri["total_mktcap_prev"] == 2.7e12
    assert ri["fng_now"] == 20 and ri["fng_7d_avg"] == 17.0  # mean(14, 20)


# --------------------------------------------------------------------------- #
# CMC-native candle wrapper (Phase 4)
# --------------------------------------------------------------------------- #
def test_fetch_daily_cmc_returns_frame(monkeypatch):
    import pandas as pd

    from ictbot.data import cmc as cmc_mod
    from ictbot.data import cmc_intel as ci_mod

    df = pd.DataFrame(
        {
            "time": [pd.Timestamp("2026-06-07")],
            "open": [1.0],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "volume": [10.0],
        }
    )
    monkeypatch.setattr(ci_mod, "daily_ohlcv", lambda symbol, days=730: df)
    monkeypatch.setattr(cmc_mod.cache, "write", lambda *a, **k: None)  # no disk
    out = cmc_mod.fetch_daily_cmc("BNB")
    assert list(out.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert out["close"].iloc[0] == 1.5


def test_fetch_daily_cmc_none_when_unavailable(monkeypatch):
    from ictbot.data import cmc as cmc_mod
    from ictbot.data import cmc_intel as ci_mod

    monkeypatch.setattr(ci_mod, "daily_ohlcv", lambda symbol, days=730: None)
    monkeypatch.setattr(cmc_mod.cache, "read", lambda *a, **k: None)
    assert cmc_mod.fetch_daily_cmc("ZZZ") is None
