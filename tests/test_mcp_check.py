"""MCP verification (scripts/mcp_check.py + cmc_agent_hub.live_tools/ping): the pure assess_pairing
classifier (READY when live+flag-off, PAIRED when enabled+live+consumed, DEGRADED when enabled but a
tool is down, OFF when flag off), the SKILL_TOOLS↔12-tool integrity, the render headline, and the
live_tools/ping helpers. Fully offline — mocked flags/tools/journal + monkeypatched _rpc; no network."""

from __future__ import annotations

import scripts.mcp_check as mc
from ictbot.data import cmc_agent_hub as hub

ALL_LIVE = list(mc.EXPECTED_TOOLS)
_FLAGS_OFF = {
    "cmc_api_key": True,
    "alloc_ta_enabled": False,
    "alloc_ta_w_rank": 1.0,
    "cmc_skill_regime": False,
    "cmc_deriv_brake": False,
    "cmc_macro_guard": False,
    "cmc_news_enabled": False,
    "cmc_mktcap_ta": False,
    "cmc_quotes_xcheck": False,
    "cmc_intel_enabled": False,
}
_FLAGS_ON = {
    **_FLAGS_OFF,
    "alloc_ta_enabled": True,
    "cmc_skill_regime": True,
    "cmc_deriv_brake": True,
    "cmc_macro_guard": True,
    "cmc_news_enabled": True,
    "cmc_mktcap_ta": True,
    "cmc_quotes_xcheck": True,
    "cmc_intel_enabled": True,
}
_LAST_FULL = {
    "ta_source": "cmc+skill",
    "ta_rank_used": True,
    "cmc_intel_used": True,
    "fear_greed": 20,
    "cmc_skill": {
        "tools_used": [
            "get_global_crypto_derivatives_metrics",
            "get_upcoming_macro_events",
            "get_crypto_latest_news",
            "get_crypto_marketcap_technical_analysis",
            "get_crypto_quotes_latest",
        ]
    },
}


def _v(rows, skill):
    return next(r["verdict"] for r in rows if r["skill"] == skill)


def test_skill_tools_integrity():
    for s in mc.SKILL_TOOLS:
        for t in s["tools"]:
            assert t in mc.EXPECTED_TOOLS, f"{s['skill']} maps to non-existent tool {t}"
    # the headline TA + skill tools are covered
    mapped = {t for s in mc.SKILL_TOOLS for t in s["tools"]}
    assert {"get_crypto_technical_analysis", "get_global_metrics_latest"} <= mapped


def test_flag_off_but_reachable_is_ready():
    rows = mc.assess_pairing(_FLAGS_OFF, ALL_LIVE, [])  # key present, all feature flags off
    assert _v(rows, "Basket TA → deploy cap") == "READY"  # mcp tool live, flag off
    assert (
        _v(rows, "Regime intel (dominance/mktcap)") == "READY"
    )  # pro-api reachable (key), flag off


def test_off_when_no_key_or_tool_down():
    rows = mc.assess_pairing({**_FLAGS_OFF, "cmc_api_key": False}, [], [])  # no key, nothing live
    assert _v(rows, "Regime intel (dominance/mktcap)") == "OFF"  # pro-api, no key
    assert _v(rows, "Fear & Greed") == "OFF"  # needs key
    assert _v(rows, "Basket TA → deploy cap") == "OFF"  # flag off + tool down


def test_fully_on_and_consumed_is_paired():
    rows = mc.assess_pairing(_FLAGS_ON, ALL_LIVE, [_LAST_FULL])
    for skill in (
        "Basket TA → deploy cap",
        "Token TA → ranking tilt",
        "Market-overview skill",
        "Regime intel (dominance/mktcap)",
        "Fear & Greed",
        "Derivatives stress",
        "Macro guard",
        "News brake",
        "Market-cap TA",
        "Quotes cross-check",
    ):
        assert _v(rows, skill) == "PAIRED", skill


def test_enabled_but_tool_down_is_degraded():
    live = [t for t in ALL_LIVE if t != "get_crypto_technical_analysis"]  # TA tool offline
    rows = mc.assess_pairing(_FLAGS_ON, live, [_LAST_FULL])
    assert _v(rows, "Basket TA → deploy cap") == "DEGRADED"  # enabled, tool down → local fallback
    assert _v(rows, "Derivatives stress") == "PAIRED"  # its tool is still live


def test_render_headline_live_vs_down():
    health = {
        "enabled": True,
        "tools_live": 12,
        "tools": ALL_LIVE,
        "sample_ok": True,
        "last_error": None,
    }
    up = mc.render_report(
        health, mc.assess_pairing(_FLAGS_ON, ALL_LIVE, [_LAST_FULL]), now_iso="t", last_ts="t0"
    )
    assert "MCP **LIVE**" in up and "12/12 tools" in up and "✅ PAIRED" in up
    down = mc.render_report(
        {
            "enabled": True,
            "tools_live": 0,
            "tools": [],
            "sample_ok": False,
            "last_error": "http 503",
        },
        [],
        now_iso="t",
        last_ts=None,
    )
    assert "MCP **DOWN**" in down


# --- cmc_agent_hub.live_tools / ping ---------------------------------------------------------- #
def test_live_tools_parses_tools_list(monkeypatch):
    monkeypatch.setattr(hub, "enabled", lambda: True)
    monkeypatch.setattr(
        hub,
        "_rpc",
        lambda method, params, **k: {
            "result": {
                "tools": [
                    {"name": "get_crypto_technical_analysis"},
                    {"name": "get_global_metrics_latest"},
                    {"noname": 1},
                ]
            }
        },
    )
    assert hub.live_tools() == ["get_crypto_technical_analysis", "get_global_metrics_latest"]


def test_live_tools_disabled_is_empty(monkeypatch):
    monkeypatch.setattr(hub, "enabled", lambda: False)  # _rpc short-circuits to None
    assert hub.live_tools() == []


def test_ping_disabled(monkeypatch):
    monkeypatch.setattr(hub, "enabled", lambda: False)
    p = hub.ping()
    assert p["enabled"] is False and p["tools_live"] == 0 and p["sample_ok"] is False


def test_ping_up(monkeypatch):
    monkeypatch.setattr(hub, "enabled", lambda: True)
    monkeypatch.setattr(hub, "live_tools", lambda: ["get_global_metrics_latest"])
    monkeypatch.setattr(hub, "global_metrics", lambda: {"data": 1})
    p = hub.ping()
    assert p["enabled"] is True and p["tools_live"] == 1 and p["sample_ok"] is True
