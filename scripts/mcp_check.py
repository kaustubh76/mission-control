#!/usr/bin/env python3
"""
MCP verification — PROVE the CMC MCP is live and each skill is paired to a responding tool.

The bot talks to CMC's hosted MCP server (HTTP JSON-RPC at mcp.coinmarketcap.com/mcp,
`X-CMC-MCP-API-KEY`) via src/ictbot/data/cmc_agent_hub.py and consumes the pre-computed tools in the
deploy cap + ranking. This does a LIVE probe (one `tools/list` + one sample `tools/call`), maps each
SKILL → its MCP tool(s) + gating flag, and reports per skill whether it is PAIRED (enabled + tools
live + consumed last tick), LIVE, READY (live but flag off), DEGRADED (enabled but a tool is down →
local fallback), or OFF. Headline: "MCP LIVE — N/12 tools, M skills paired".

Honest scope: this is CMC's HTTP-JSON-RPC branded as "MCP" (no `mcp` SDK / stdio). The Crypto.com MCP
in claude.ai is a session tool the standalone cron bot cannot reach (not wired, by design). See
docs/mcp_wiring.md. READ-ONLY: a live tools/list + one cheap tools/call (a few CMC credits); no trades,
no flag changes.

Usage:
  make mcp_check
  PYTHONPATH=src:. python scripts/mcp_check.py [--no-save]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ictbot.data import cmc_agent_hub as hub
from ictbot.settings import DATA_DIR, JOURNAL_DIR, settings

REPORT_PATH = DATA_DIR / "reports" / "mcp_status.md"

# The 12 callable CMC Data-MCP tools (cmc_agent_hub docstring / probe_agent_hub).
EXPECTED_TOOLS = (
    "get_crypto_quotes_latest", "get_crypto_info", "search_cryptos", "search_crypto_info",
    "get_crypto_technical_analysis", "get_crypto_marketcap_technical_analysis", "get_crypto_metrics",
    "get_global_metrics_latest", "get_global_crypto_derivatives_metrics", "trending_crypto_narratives",
    "get_upcoming_macro_events", "get_crypto_latest_news",
)

# Canonical SKILL ↔ MCP-tool(s) ↔ gating-flag pairing. `evidence` = the journal field that proves the
# skill was consumed last tick (None → check the market-overview skill's `cmc_skill.tools_used`).
SKILL_TOOLS = [
    {"skill": "Basket TA → deploy cap", "tools": ["get_crypto_technical_analysis"],
     "flag": "alloc_ta_enabled", "transport": "mcp", "evidence": "ta_source"},
    {"skill": "Token TA → ranking tilt", "tools": ["get_crypto_technical_analysis"],
     "flag": "alloc_ta_enabled", "transport": "mcp", "evidence": "ta_rank_used"},
    {"skill": "Market-overview skill", "tools": ["get_crypto_technical_analysis",
     "get_global_metrics_latest", "trending_crypto_narratives"],
     "flag": "cmc_skill_regime", "transport": "mcp", "evidence": "cmc_skill"},
    {"skill": "Derivatives stress", "tools": ["get_global_crypto_derivatives_metrics"],
     "flag": "cmc_deriv_brake", "transport": "mcp", "evidence": None},
    {"skill": "Macro guard", "tools": ["get_upcoming_macro_events"],
     "flag": "cmc_macro_guard", "transport": "mcp", "evidence": None},
    {"skill": "News brake", "tools": ["get_crypto_latest_news"],
     "flag": "cmc_news_enabled", "transport": "mcp", "evidence": None},
    {"skill": "Market-cap TA", "tools": ["get_crypto_marketcap_technical_analysis"],
     "flag": "cmc_mktcap_ta", "transport": "mcp", "evidence": None},
    {"skill": "Quotes cross-check", "tools": ["get_crypto_quotes_latest"],
     "flag": "cmc_quotes_xcheck", "transport": "mcp", "evidence": None},
    {"skill": "Regime intel (dominance/mktcap)", "tools": [],
     "flag": "cmc_intel_enabled", "transport": "pro-api", "evidence": "cmc_intel_used"},
    {"skill": "Fear & Greed", "tools": [], "flag": None, "transport": "pro-api", "evidence": "fear_greed"},
]

_BADGE = {"PAIRED": "✅ PAIRED", "LIVE": "🟢 LIVE", "READY": "🟡 READY",
          "DEGRADED": "⚠️ DEGRADED", "OFF": "⚪ OFF"}


def flags_snapshot() -> dict:
    return {
        "cmc_api_key": bool(settings.cmc_api_key),
        "alloc_ta_enabled": settings.alloc_ta_enabled,
        "alloc_ta_w_rank": settings.alloc_ta_w_rank,
        "cmc_skill_regime": settings.cmc_skill_regime,
        "cmc_deriv_brake": settings.cmc_deriv_brake,
        "cmc_macro_guard": settings.cmc_macro_guard,
        "cmc_news_enabled": settings.cmc_news_enabled,
        "cmc_mktcap_ta": settings.cmc_mktcap_ta,
        "cmc_quotes_xcheck": settings.cmc_quotes_xcheck,
        "cmc_intel_enabled": settings.cmc_intel_enabled,
    }


def _consumed(skill: dict, last: dict):
    """Did the last journal tick actually consume this skill (True/False/None=unknown)?"""
    ev = skill.get("evidence")
    if ev == "ta_source":
        return isinstance(last.get("ta_source"), str) and "cmc" in last["ta_source"]
    if ev == "fear_greed":
        return isinstance(last.get("fear_greed"), (int, float))
    if ev == "ta_rank_used":
        return last.get("ta_rank_used") is True
    if ev == "cmc_intel_used":
        return last.get("cmc_intel_used") is True
    if ev == "cmc_skill":
        return bool(last.get("cmc_skill"))
    used = ((last.get("cmc_skill") or {}).get("tools_used")) or []   # the market-overview sub-signals
    return any(t in used for t in skill.get("tools", [])) if used else None


def _verdict(enabled: bool, tools_live: bool, consumed) -> str:
    if not enabled:
        return "READY" if tools_live else "OFF"
    if not tools_live:
        return "DEGRADED"          # enabled but tool(s) not responding → local fallback
    return "PAIRED" if consumed is True else "LIVE"


def assess_pairing(flags: dict, live_tools: list[str], recent_rows: list[dict]) -> list[dict]:
    """Pure: classify each skill's pairing given the config flags, the live tool list, and recent
    journal rows. transport='mcp' skills require their tool(s) in live_tools; 'pro-api' skills key off
    CMC_API_KEY (not surfaced in tools/list)."""
    last = recent_rows[-1] if recent_rows else {}
    lt = set(live_tools or [])
    out = []
    for s in SKILL_TOOLS:
        flag = s["flag"]
        enabled = bool(flags.get("cmc_api_key")) if flag is None else bool(flags.get(flag))
        if s["transport"] == "mcp":
            tools_live = bool(s["tools"]) and all(t in lt for t in s["tools"])
        else:
            tools_live = bool(flags.get("cmc_api_key"))
        consumed = _consumed(s, last)
        out.append({**s, "enabled": enabled, "tools_live": tools_live, "consumed": consumed,
                    "verdict": _verdict(enabled, tools_live, consumed)})
    return out


def render_report(health: dict, assessed: list[dict], *, now_iso: str, last_ts: str | None) -> str:
    n_tools, n_exp = health["tools_live"], len(EXPECTED_TOOLS)
    paired = sum(1 for a in assessed if a["verdict"] == "PAIRED")
    up = health["enabled"] and n_tools > 0 and health["sample_ok"]
    headline = (f"MCP **LIVE** — {n_tools}/{n_exp} tools, {paired} skill(s) paired" if up
                else ("MCP **DOWN** — " + (health.get("last_error") or "no tools responding")
                      if health["enabled"] else "MCP **FLAG-OFF** (CMC_MCP_ENABLED off / no key)"))
    missing = [t for t in EXPECTED_TOOLS if t not in set(health["tools"])]
    out = [
        "# MCP status — wiring + skill pairing (live-verified)",
        "",
        f"_Generated by `make mcp_check` at **{now_iso}** (last journal tick: {last_ts or '—'})._",
        "",
        f"## {headline}",
        "",
        "The bot reads CMC's hosted MCP (HTTP JSON-RPC, `X-CMC-MCP-API-KEY`) via "
        "`src/ictbot/data/cmc_agent_hub.py`; skills fall back to local compute if a tool is down (never a "
        "hard failure). Crypto.com's claude.ai MCP is a session tool the cron bot can't reach (see "
        "[mcp_wiring.md](mcp_wiring.md)).",
        "",
        "| Skill | MCP tool(s) | Flag | Enabled | Tools live | Consumed last tick | Verdict |",
        "|---|---|---|:--:|:--:|:--:|:--:|",
    ]
    for a in assessed:
        tools = ", ".join(f"`{t}`" for t in a["tools"]) or "_(Pro API)_"
        flag = f"`{a['flag'].upper()}`" if a["flag"] else "_(needs key)_"
        consumed = {True: "✅", False: "—", None: "·"}[a["consumed"]]
        out.append(f"| {a['skill']} | {tools} | {flag} | {'✅' if a['enabled'] else '⚪'} | "
                   f"{'✅' if a['tools_live'] else '⚠️'} | {consumed} | {_BADGE[a['verdict']]} |")
    if missing:
        out += ["", f"**Tools not in the live `tools/list`:** {', '.join(missing)}"]
    out += [
        "",
        "_PAIRED = enabled + tool live + consumed last tick · LIVE = enabled + tool live · READY = tool "
        "live but flag off · DEGRADED = enabled but tool down (local fallback) · OFF = flag off / no key. "
        "Probe-only — no flags changed. Enablement: [cmc_enablement.md](cmc_enablement.md)._",
        "",
    ]
    return "\n".join(out)


def _recent_rebalances(limit: int = 30) -> list[dict]:
    p = JOURNAL_DIR / "allocator_journal.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") == "REBALANCE":
            rows.append(r)
    return rows[-limit:]


def run_mcp_check(*, save: bool = True, report_path: Path = REPORT_PATH,
                  now_iso: str | None = None) -> tuple[dict, list[dict]]:
    now_iso = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    health = hub.ping()
    rows = _recent_rebalances()
    assessed = assess_pairing(flags_snapshot(), health["tools"], rows)
    if save:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        last_ts = rows[-1].get("ts") if rows else None
        report_path.write_text(render_report(health, assessed, now_iso=now_iso, last_ts=last_ts),
                               encoding="utf-8")
    return health, assessed


def main() -> int:
    ap = argparse.ArgumentParser(description="Live-verify the CMC MCP wiring + skill pairing.")
    ap.add_argument("--no-save", action="store_true", help="print only; don't write the report")
    args = ap.parse_args()

    health, assessed = run_mcp_check(save=not args.no_save)
    n_tools, paired = health["tools_live"], sum(1 for a in assessed if a["verdict"] == "PAIRED")
    if not health["enabled"]:
        print(f"MCP FLAG-OFF — {health['last_error']}")
    elif n_tools and health["sample_ok"]:
        print(f"MCP LIVE ✅ — {n_tools}/{len(EXPECTED_TOOLS)} tools, sample call OK, {paired} skill(s) paired\n")
    else:
        print(f"MCP DOWN ⚠️ — tools_live={n_tools} sample_ok={health['sample_ok']} "
              f"err={health.get('last_error')}\n")
    print(f"{'skill':32} {'enabled':>7} {'live':>5} {'used':>5}  verdict")
    print("-" * 70)
    for a in assessed:
        print(f"{a['skill']:32} {('yes' if a['enabled'] else 'no'):>7} "
              f"{('yes' if a['tools_live'] else 'no'):>5} "
              f"{({True: 'yes', False: 'no', None: '?'}[a['consumed']]):>5}  {_BADGE[a['verdict']]}")
    if not args.no_save:
        print(f"\nwrote: {REPORT_PATH}")
    print("\nREAD-ONLY probe — no flags changed, nothing live touched. Detail: docs/mcp_wiring.md")
    return 0 if (health["enabled"] and n_tools) else 1


if __name__ == "__main__":
    sys.exit(main())
