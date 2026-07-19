"""CMC Agent Hub client — MCP tool parsing + the market-overview skill (no network)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from ictbot.data import cmc_agent_hub as hub

# Canned MCP payloads (real shapes captured from the live server).
_TA = {
    "rsi": {"rsi14": "38.44"},
    "macd": {"histogram": "-7.71"},
    "moving_averages": {"exponential_moving_average_30_day": "630.86"},
    "pivotPoint": "587.99",
}
_TA_BULL = {
    "rsi": {"rsi14": "55.0"},
    "macd": {"histogram": "+4.2"},
    "moving_averages": {"exponential_moving_average_30_day": "600.0"},
    "pivotPoint": "640.0",
}
_GM = {
    "sentiment": {"fear_greed": {"current": {"index": 15}}},
    "dominance": {"btc": {"current": "+58.42%"}},
    "market_size": {"total_crypto_market_cap_usd": {"percent_change": {"24h": "+0.71179%"}}},
}
_TRENDING = {
    "categoryList": {
        "headers": ["trendingRank", "slug", "x", "categoryName"],
        "rows": [[1, "a", "u", "Binance Ecosystem"], [2, "b", "u", "Layer 1"]],
    }
}


def _wrap(payload):
    return {"result": {"content": [{"text": json.dumps(payload)}]}}


@pytest.fixture
def hub_on(monkeypatch):
    monkeypatch.setattr(hub.settings, "cmc_mcp_enabled", True, raising=False)
    monkeypatch.setattr(hub.settings, "cmc_api_key", "k", raising=False)
    hub._cache.clear()

    def fake_rpc(method, params, timeout=30.0):
        if method == "tools/list":
            return {"result": {"tools": [{"name": "get_crypto_technical_analysis"}]}}
        name = params.get("name")
        if name == "get_crypto_technical_analysis":
            return _wrap(_TA)
        if name == "get_global_metrics_latest":
            return _wrap(_GM)
        if name == "trending_crypto_narratives":
            return _wrap(_TRENDING)
        return None

    monkeypatch.setattr(hub, "_rpc", fake_rpc)
    return hub


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(hub.settings, "cmc_mcp_enabled", False, raising=False)
    assert hub.enabled() is False
    assert hub.market_overview() is None


def test_technical_analysis_resolves_symbol(hub_on):
    ta = hub_on.technical_analysis("BNB")
    assert ta["rsi"]["rsi14"] == "38.44"
    assert hub_on.technical_analysis("doesnotexist") is None  # unknown symbol → None


def test_table_tool_parsed_to_dicts():
    rows = hub._table_to_dicts({"headers": ["a", "b"], "rows": [[1, 2], [3, 4]]})
    assert rows == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


def test_global_metrics_nested_parsers(hub_on):
    gm = hub_on.global_metrics()
    assert hub.gm_fear_greed(gm) == 15
    assert hub.gm_btc_dominance(gm) == 58.42
    assert hub.gm_mktcap_change_24h(gm) == pytest.approx(0.71179)


def test_trending_narratives_extracts_names(hub_on):
    assert hub_on.trending_narratives(2) == ["Binance Ecosystem", "Layer 1"]


def test_token_ta_scores_per_token(hub_on):
    # Per-token CMC TA confirmation: 8 tokens, all bearish (_TA: macd<0, below EMA, RSI 38).
    scores = hub_on.token_ta_scores()
    assert len(scores) == 8
    for v in scores.values():
        assert 0.0 <= v <= 0.3  # bearish TA -> low confirmation
    # formula: 0.25 * rsi_health(38.44) only (macd<0, below-EMA, not overbought) ≈ 0.115
    assert all(abs(v - 0.115) < 0.01 for v in scores.values())


def test_market_overview_composes_skill(hub_on):
    mo = hub_on.market_overview()
    assert 0.0 <= mo["risk_budget"] <= 1.0
    assert mo["fear_greed"] == 15
    assert mo["regime"] == "risk-off"  # extreme fear + all-bearish TA
    assert "get_crypto_technical_analysis" in mo["tools_used"]
    assert mo["narratives"] == ["Binance Ecosystem", "Layer 1"]
    assert mo["ta_breadth"]["tokens"] == 8  # all 8 contest tokens read


def test_market_overview_risk_on_when_bullish(monkeypatch):
    monkeypatch.setattr(hub.settings, "cmc_mcp_enabled", True, raising=False)
    monkeypatch.setattr(hub.settings, "cmc_api_key", "k", raising=False)
    hub._cache.clear()
    greedy_gm = {
        "sentiment": {"fear_greed": {"current": {"index": 80}}},
        "dominance": {"btc": {"current": "+50%"}},
        "market_size": {"total_crypto_market_cap_usd": {"percent_change": {"24h": "+2.0%"}}},
    }

    def fake_rpc(method, params, timeout=30.0):
        name = params.get("name") if method == "tools/call" else None
        if name == "get_crypto_technical_analysis":
            return _wrap(_TA_BULL)
        if name == "get_global_metrics_latest":
            return _wrap(greedy_gm)
        if name == "trending_crypto_narratives":
            return _wrap(_TRENDING)
        return None

    monkeypatch.setattr(hub, "_rpc", fake_rpc)
    mo = hub.market_overview()
    assert mo["regime"] == "risk-on" and mo["risk_budget"] >= 0.6


# --------------------------------------------------------------------------- #
# Extra CMC Data-MCP tools wired into the composed skill (WS-2)
# --------------------------------------------------------------------------- #
_DERIV = {
    "totalOpenInterest": {"current": "360.64 B", "percentage_change_24h": "+30%"},
    "fundingRate": {"current": "0.03"},
}  # OI surging + stretched funding
_MKTCAP = {
    "macd": {"histogram": "-2.87 B"},
    "rsi": {"rsi14": "25.06"},
    "currentMarketCap": "2.18 T",
}  # oversold global mktcap
# Relative-future dates so `next_macro_event` always picks the high-impact Fed event as the
# nearest upcoming one — a fixed calendar date silently rots once it slips into the past
# (next_macro_event drops events older than -12h), flipping next_macro_event to the low-impact
# unlock. Fed = now+1d (nearest), unlock = now+4d. `%d %B %Y` matches hub._parse_event_date.
_FED_DATE = (datetime.now() + timedelta(days=1)).strftime("%d %B %Y")
_UNLOCK_DATE = (datetime.now() + timedelta(days=4)).strftime("%d %B %Y")
_MACRO = {
    "upcomingEventNews": {
        "headers": ["title", "content", "url", "eventDate"],
        "rows": [
            ["Fed Interest Rate Decision Announcement", "c", "u", _FED_DATE],
            ["Minor token unlock", "c", "u", _UNLOCK_DATE],
        ],
    }
}
_NEWS = [
    {"title": "BNB rallies on ecosystem growth", "url": "u1", "publishedAt": "t1"},
    {"title": "Protocol exploit drains funds", "url": "u2", "publishedAt": "t2"},
]
_QUOTES = [
    {
        "id": str(cid),
        "symbol": sym,
        "price": 100.0 + i,
        "percent_change_24h": 1.0,
        "percent_change_7d": 2.0,
        "volume_24h": 1e6,
        "market_cap": 1e9,
    }
    for i, (sym, cid) in enumerate(hub.CMC_IDS.items())
]


def _full_rpc(method, params, timeout=30.0):
    if method == "tools/list":
        return {"result": {"tools": [{"name": "get_crypto_technical_analysis"}]}}
    name = params.get("name")
    payloads = {
        "get_crypto_technical_analysis": _TA,
        "get_global_metrics_latest": _GM,
        "trending_crypto_narratives": _TRENDING,
        "get_crypto_marketcap_technical_analysis": _MKTCAP,
        "get_global_crypto_derivatives_metrics": _DERIV,
        "get_upcoming_macro_events": _MACRO,
        "get_crypto_latest_news": _NEWS,
        "get_crypto_quotes_latest": _QUOTES,
    }
    return _wrap(payloads[name]) if name in payloads else None


@pytest.fixture
def hub_full(monkeypatch):
    """MCP on, ALL extra levers on, every tool mocked."""
    monkeypatch.setattr(hub.settings, "cmc_mcp_enabled", True, raising=False)
    monkeypatch.setattr(hub.settings, "cmc_api_key", "k", raising=False)
    for flag in (
        "cmc_mktcap_ta",
        "cmc_deriv_brake",
        "cmc_macro_guard",
        "cmc_quotes_xcheck",
        "cmc_news_enabled",
    ):
        monkeypatch.setattr(hub.settings, flag, True, raising=False)
    hub._cache.clear()
    monkeypatch.setattr(hub, "_rpc", _full_rpc)
    return hub


def test_unit_num_parser():
    assert hub._unit_num("360.64 B") == pytest.approx(360.64e9)
    assert hub._unit_num("2.18 T") == pytest.approx(2.18e12)
    assert hub._unit_num("203.04 T") == pytest.approx(203.04e12)
    assert hub._unit_num(42) == 42.0
    assert hub._unit_num("$1.5M") == pytest.approx(1.5e6)
    assert hub._unit_num(None) is None
    assert hub._unit_num("n/a") is None


def test_derivatives_stress(hub_full):
    stress, detail = hub_full.derivatives_stress()
    # OI +30% (term=1.0) & funding 0.03 (term=0.6) -> 0.7*1 + 0.3*0.6 = 0.88
    assert stress == pytest.approx(0.88, abs=0.01)
    assert detail["oi_change_24h"] == 30.0
    assert detail["open_interest_usd"] == pytest.approx(360.64e9)


def test_mktcap_technical_analysis(hub_full):
    mt = hub_full.mktcap_technical_analysis()
    assert mt["rsi14"] == pytest.approx(25.06)
    assert mt["macd_histogram"] == pytest.approx(-2.87e9)
    assert mt["health"] == pytest.approx(0.25 - 0.05, abs=0.01)  # rsi/100 minus macd<0 nudge


def test_macro_events_high_impact_detection(hub_full):
    evs = hub_full.macro_events()
    assert evs[0]["high_impact"] is True  # "Fed Interest Rate Decision"
    assert evs[1]["high_impact"] is False  # "Minor token unlock"
    assert hub._parse_event_date("17 June 2026").month == 6


def test_quotes_latest_and_verify_ids(hub_full):
    q = hub_full.quotes_latest()
    assert len(q) == 8 and q["BNB"]["symbol_cmc"] == "BNB"
    ids = hub_full.verify_cmc_ids()
    assert ids["matched"] == 8 and ids["total"] == 8 and ids["mismatches"] == {}


def test_market_overview_composes_all_levers(hub_full):
    mo = hub_full.market_overview()
    assert mo["skill_source"] == "composed"
    # every extra tool was actually called and recorded
    for t in (
        "get_crypto_marketcap_technical_analysis",
        "get_global_crypto_derivatives_metrics",
        "get_upcoming_macro_events",
        "get_crypto_quotes_latest",
        "get_crypto_latest_news",
    ):
        assert t in mo["tools_used"]
    assert mo["derivatives"]["stress"] == pytest.approx(0.88, abs=0.01)
    assert mo["mktcap_ta"]["health"] is not None
    assert len(mo["quotes_cross_check"]) == 8
    assert len(mo["top_news"]) == 2
    assert mo["next_macro_event"]["high_impact"] is True
    assert 0.0 <= mo["risk_budget"] <= 1.0


def test_deriv_brake_lowers_budget(monkeypatch):
    """The derivatives brake must strictly reduce the risk budget vs the brake-off baseline."""
    base = {"cmc_mcp_enabled": True, "cmc_api_key": "k"}
    for k, v in base.items():
        monkeypatch.setattr(hub.settings, k, v, raising=False)
    monkeypatch.setattr(hub, "_rpc", _full_rpc)

    monkeypatch.setattr(hub.settings, "cmc_deriv_brake", False, raising=False)
    hub._cache.clear()
    b_off = hub.market_overview()["risk_budget"]

    monkeypatch.setattr(hub.settings, "cmc_deriv_brake", True, raising=False)
    hub._cache.clear()
    b_on = hub.market_overview()["risk_budget"]
    assert b_on < b_off  # high OI stress brakes the budget


def test_macro_guard_haircut(monkeypatch):
    """A high-impact event inside the horizon haircuts the budget; outside it does not."""
    monkeypatch.setattr(hub.settings, "cmc_mcp_enabled", True, raising=False)
    monkeypatch.setattr(hub.settings, "cmc_api_key", "k", raising=False)
    monkeypatch.setattr(hub.settings, "cmc_macro_guard", True, raising=False)
    monkeypatch.setattr(hub.settings, "cmc_macro_guard_hours", 36.0, raising=False)
    monkeypatch.setattr(hub, "_rpc", _full_rpc)

    monkeypatch.setattr(
        hub,
        "next_macro_event",
        lambda: {
            "title": "FOMC",
            "hours_to": 200.0,
            "high_impact": True,
            "event_date": "x",
            "url": None,
        },
    )
    hub._cache.clear()
    far = hub.market_overview()["risk_budget"]

    monkeypatch.setattr(
        hub,
        "next_macro_event",
        lambda: {
            "title": "FOMC",
            "hours_to": 10.0,
            "high_impact": True,
            "event_date": "x",
            "url": None,
        },
    )
    hub._cache.clear()
    near = hub.market_overview()["risk_budget"]
    assert near < far  # de-risk INTO the event


def test_skill_source_is_composed():
    assert hub.SKILL_SOURCE == "composed"
