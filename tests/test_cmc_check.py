"""cmc_check diagnostic (scripts/cmc_check.py): the pure assess_cmc classifier — baseline (all
levers off → FLAG-OFF, F&G degraded without a key), and fully-lit (key + flags on + journal evidence
→ LIVE) — plus the render. Fully offline — synthetic flags/capability/rows, no network, no flags
touched."""

from __future__ import annotations

import scripts.cmc_check as cc

_OFF = {
    "cmc_api_key": False,
    "cmc_intel_enabled": False,
    "cmc_regime_enhanced": False,
    "alloc_ta_enabled": False,
    "alloc_ta_w_cap": 1.0,
    "alloc_ta_w_rank": 1.0,
    "cmc_skill_regime": False,
    "cmc_mcp_enabled": False,
    "x402_enabled": False,
}

_ON = {
    "cmc_api_key": True,
    "cmc_intel_enabled": True,
    "cmc_regime_enhanced": True,
    "alloc_ta_enabled": True,
    "alloc_ta_w_cap": 1.0,
    "alloc_ta_w_rank": 1.0,
    "cmc_skill_regime": True,
    "cmc_mcp_enabled": True,
    "x402_enabled": True,
}


def _status(rows, source):
    return next(r["status"] for r in rows if r["source"] == source)


def test_baseline_all_levers_flag_off_and_fg_missing_without_key():
    rows = cc.assess_cmc(_OFF, {}, [])
    assert _status(rows, "CMC_API_KEY") == "MISSING"
    assert _status(rows, "Fear & Greed → regime") == "MISSING"  # no key
    assert _status(rows, "Regime intel (dominance/mktcap)") == "FLAG-OFF"
    assert _status(rows, "TA → deploy cap") == "FLAG-OFF"
    assert _status(rows, "Market-overview skill") == "FLAG-OFF"
    assert _status(rows, "x402 DEX data") == "FLAG-OFF"
    assert _status(rows, "4h candles (momentum rank)") == "NOT-CMC"  # always Binance


def test_degraded_fear_greed_when_key_set_but_none_flowed():
    rows = cc.assess_cmc({**_OFF, "cmc_api_key": True}, {}, [{"fear_greed": None}])
    assert _status(rows, "CMC_API_KEY") == "SET"
    assert _status(rows, "Fear & Greed → regime") == "DEGRADED"  # key set, but None last tick


def test_fully_lit_reports_live_from_journal_evidence():
    last = {
        "fear_greed": 38,
        "cmc_intel_used": True,
        "ta_health": 0.6,
        "ta_source": "cmc",
        "ta_rank_used": True,
        "cmc_skill": {"risk_budget": 0.5},
    }
    rows = cc.assess_cmc(_ON, {"/x": {"ok": True}, "/y": {"ok": False}}, [last])
    assert _status(rows, "Fear & Greed → regime") == "LIVE"
    assert _status(rows, "Regime intel (dominance/mktcap)") == "LIVE"
    assert _status(rows, "TA → deploy cap") == "LIVE"
    assert _status(rows, "TA → ranking tilt") == "LIVE"
    assert _status(rows, "Market-overview skill") == "LIVE"
    assert _status(rows, "x402 DEX data") == "ENRICH-ONLY"  # on, but never drives trades
    assert _status(rows, "CMC_API_KEY") == "SET"


def test_enabled_but_idle_is_on_not_live():
    # flag on but the last tick did not record using it → ON (not LIVE, not FLAG-OFF).
    rows = cc.assess_cmc(_ON, {}, [{"cmc_intel_used": False}])
    assert _status(rows, "Regime intel (dominance/mktcap)") == "ON"


def test_mcp_row_is_live_verified_not_just_the_flag():
    # the MCP row now reflects a live ping (tools/list + sample call), not the raw flag.
    live = {"enabled": True, "tools_live": 12, "tools": [], "sample_ok": True, "last_error": None}
    assert _status(cc.assess_cmc(_ON, {}, [], live), "CMC MCP agent-hub") == "LIVE"
    down = {
        "enabled": True,
        "tools_live": 0,
        "tools": [],
        "sample_ok": False,
        "last_error": "http 503",
    }
    assert _status(cc.assess_cmc(_ON, {}, [], down), "CMC MCP agent-hub") == "DOWN"
    off = {
        "enabled": False,
        "tools_live": 0,
        "tools": [],
        "sample_ok": False,
        "last_error": "disabled",
    }
    assert _status(cc.assess_cmc(_ON, {}, [], off), "CMC MCP agent-hub") == "FLAG-OFF"


def test_render_report_has_table_and_measure_first_framing():
    rpt = cc.render_report(cc.assess_cmc(_OFF, {}, []), now_iso="t", last_ts=None)
    assert "CMC status" in rpt and "Measure-first" in rpt and "cmc_enablement.md" in rpt
    assert "FLAG-OFF" in rpt and "| Source / skill |" in rpt
