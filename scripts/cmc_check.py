#!/usr/bin/env python3
"""
CMC measure-first diagnostic — what CMC data/skills are ACTUALLY flowing into decisions.

The rich CMC layers (regime intel, TA cap+rank, the composed market-overview skill, x402) are all
WIRED but FLAG-OFF by default so the validated contest baseline stays bit-for-bit; Fear&Greed is live
iff CMC_API_KEY is set; 4h candles are Binance (CMC intraday is tier-gated). This reports, for the
CURRENT config, each source → flag · enabled? · status (LIVE / ON / FLAG-OFF / DEGRADED / MISSING /
ENRICH-ONLY / NOT-CMC) · what last flowed (from the journal's cmc_* enrichment fields). It does NOT
flip any flag or trade — measure-first only. To MEASURE the backtestable levers' PnL effect, run
`make ab_regime`; to enable a lever, see docs/cmc_enablement.md (enable → SIM-validate → sign-off).

Usage:
  make cmc_check
  PYTHONPATH=src:. python scripts/cmc_check.py [--no-save]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ictbot.settings import DATA_DIR, JOURNAL_DIR, settings

REPORT_PATH = DATA_DIR / "reports" / "cmc_status.md"
CAPABILITY_PATH = JOURNAL_DIR / "cmc_capability.json"

_BADGE = {"LIVE": "✅ LIVE", "ON": "🟢 ON", "FLAG-OFF": "⚪ FLAG-OFF", "DEGRADED": "⚠️ DEGRADED",
          "MISSING": "❌ MISSING", "ENRICH-ONLY": "📊 ENRICH-ONLY", "NOT-CMC": "➖ NOT-CMC",
          "SET": "✅ SET", "DOWN": "⚠️ DOWN"}


def _mcp_row(flags: dict, mcp_health: dict | None):
    """The CMC MCP row — LIVE-VERIFIED via cmc_agent_hub.ping() (tools/list + a sample call) when a
    health dict is supplied, else the raw flag. Run `make mcp_check` for the per-skill pairing."""
    on = bool(flags.get("cmc_mcp_enabled"))
    if mcp_health is None:
        return _row("CMC MCP agent-hub", "CMC_MCP_ENABLED", on, _onoff(on, None),
                    "enables live TA/skill reads; else local 4h-resample fallback "
                    "(run `make mcp_check` to live-verify)")
    n = mcp_health.get("tools_live", 0)
    if mcp_health.get("enabled") and n and mcp_health.get("sample_ok"):
        return _row("CMC MCP agent-hub", "CMC_MCP_ENABLED", True, "LIVE",
                    f"{n}/12 tools live, sample call OK — skills paired (run `make mcp_check`)")
    if mcp_health.get("enabled"):
        return _row("CMC MCP agent-hub", "CMC_MCP_ENABLED", True, "DOWN",
                    f"enabled but unreachable: {mcp_health.get('last_error')} → local fallback")
    return _row("CMC MCP agent-hub", "CMC_MCP_ENABLED", False, "FLAG-OFF",
                "CMC_MCP_ENABLED off / no key → skills use local compute")


def _row(source, flag, enabled, status, note):
    return {"source": source, "flag": flag, "enabled": enabled, "status": status, "note": note}


def _onoff(on: bool, used) -> str:
    """FLAG-OFF if disabled; LIVE if enabled + last tick used it; ON if enabled but idle/unknown."""
    if not on:
        return "FLAG-OFF"
    return "LIVE" if used is True else "ON"


def flags_snapshot() -> dict:
    """The CMC-relevant settings, snapshotted so assess_cmc stays pure/testable."""
    return {
        "cmc_api_key": bool(settings.cmc_api_key),
        "cmc_intel_enabled": settings.cmc_intel_enabled,
        "cmc_regime_enhanced": settings.cmc_regime_enhanced,
        "alloc_ta_enabled": settings.alloc_ta_enabled,
        "alloc_ta_w_cap": settings.alloc_ta_w_cap,
        "alloc_ta_w_rank": settings.alloc_ta_w_rank,
        "cmc_skill_regime": settings.cmc_skill_regime,
        "cmc_mcp_enabled": settings.cmc_mcp_enabled,
        "x402_enabled": settings.x402_enabled,
    }


def assess_cmc(flags: dict, capability: dict, recent_rows: list[dict],
               mcp_health: dict | None = None) -> list[dict]:
    """Pure classification of each CMC source/skill. `flags` = flags_snapshot(); `capability` =
    cmc_capability.json (probe_cmc) or {}; `recent_rows` = recent REBALANCE rows (newest last)."""
    last = recent_rows[-1] if recent_rows else {}
    key_set = bool(flags.get("cmc_api_key"))
    n_cap = sum(1 for v in capability.values() if isinstance(v, dict) and v.get("ok"))
    fg = last.get("fear_greed")
    fg_live = isinstance(fg, (int, float))
    intel_on = bool(flags.get("cmc_intel_enabled")) and bool(flags.get("cmc_regime_enhanced"))
    ta_cap_on = bool(flags.get("alloc_ta_enabled")) and float(flags.get("alloc_ta_w_cap") or 0) > 0
    ta_rank_on = bool(flags.get("alloc_ta_enabled")) and float(flags.get("alloc_ta_w_rank") or 0) > 0
    skill_on = bool(flags.get("cmc_skill_regime"))
    return [
        _row("4h candles (momentum rank)", "—", True, "NOT-CMC",
             "Binance primary+fallback; CMC intraday is tier-gated (by design)"),
        _row("CMC_API_KEY", "CMC_API_KEY", key_set, "SET" if key_set else "MISSING",
             f"{n_cap}/{len(capability)} endpoints in-tier (probe_cmc)" if capability else
             ("key present (run `make` probe_cmc to map tiers)" if key_set else
              "no key → Fear&Greed / intel / TA all blind")),
        _row("Fear & Greed → regime", "(always attempted)", True,
             "LIVE" if fg_live else ("DEGRADED" if key_set else "MISSING"),
             f"last fear_greed={fg}" if fg_live else "None → regime score = breadth+trend only"),
        _row("Regime intel (dominance/mktcap)", "CMC_INTEL_ENABLED + CMC_REGIME_ENHANCED", intel_on,
             _onoff(intel_on, last.get("cmc_intel_used")), f"cmc_intel_used={last.get('cmc_intel_used')}"),
        _row("TA → deploy cap", "ALLOC_TA_ENABLED + ALLOC_TA_W_CAP>0", ta_cap_on,
             _onoff(ta_cap_on, last.get("ta_health") is not None),
             f"ta_health={last.get('ta_health')} src={last.get('ta_source')}"),
        _row("TA → ranking tilt", "ALLOC_TA_ENABLED + ALLOC_TA_W_RANK>0", ta_rank_on,
             _onoff(ta_rank_on, last.get("ta_rank_used")), f"ta_rank_used={last.get('ta_rank_used')}"),
        _row("Market-overview skill", "CMC_SKILL_REGIME", skill_on,
             _onoff(skill_on, bool(last.get("cmc_skill"))),
             "blended into the deploy cap when on" if skill_on else "off (validated baseline)"),
        _mcp_row(flags, mcp_health),
        _row("x402 DEX data", "X402_ENABLED", bool(flags.get("x402_enabled")),
             "ENRICH-ONLY" if flags.get("x402_enabled") else "FLAG-OFF",
             "journaled for the dashboard; NEVER drives the trade"),
    ]


def render_report(rows: list[dict], *, now_iso: str, last_ts: str | None) -> str:
    out = [
        "# CMC status — what's flowing into decisions",
        "",
        f"_Generated by `make cmc_check` at **{now_iso}** (last journal tick: {last_ts or '—'})._",
        "",
        "**Measure-first.** The rich CMC levers are wired but **FLAG-OFF by default** so the validated "
        "baseline stays bit-for-bit; this reports the CURRENT config, it changes nothing. Enable a lever "
        "only via the **enable → SIM-validate → sign-off** path in "
        "[cmc_enablement.md](cmc_enablement.md); measure the backtestable levers with `make ab_regime`.",
        "",
        "| Source / skill | Flag | Status | Notes |",
        "|---|---|:--:|---|",
    ]
    for r in rows:
        out.append(f"| {r['source']} | `{r['flag']}` | {_BADGE.get(r['status'], r['status'])} | {r['note']} |")
    out += [
        "",
        "_LIVE = on + flowed last tick · ON = on but idle/unknown · FLAG-OFF = off by design (baseline) · "
        "DEGRADED = should be live but returned None · MISSING = no API key · ENRICH-ONLY = journaled, "
        "never trades · NOT-CMC = sourced elsewhere. No flags were changed._",
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


def run_cmc_check(*, save: bool = True, report_path: Path = REPORT_PATH,
                  now_iso: str | None = None) -> list[dict]:
    now_iso = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    capability = {}
    if CAPABILITY_PATH.exists():
        try:
            capability = json.loads(CAPABILITY_PATH.read_text(encoding="utf-8"))
        except Exception:
            capability = {}
    rows = _recent_rebalances()
    try:
        from ictbot.data import cmc_agent_hub
        mcp_health = cmc_agent_hub.ping()        # live-verify the MCP (tools/list + sample call)
    except Exception:
        mcp_health = None
    assessed = assess_cmc(flags_snapshot(), capability, rows, mcp_health)
    if save:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        last_ts = rows[-1].get("ts") if rows else None
        report_path.write_text(render_report(assessed, now_iso=now_iso, last_ts=last_ts), encoding="utf-8")
    return assessed


def main() -> int:
    ap = argparse.ArgumentParser(description="Report which CMC data/skills are live vs flag-off.")
    ap.add_argument("--no-save", action="store_true", help="print only; don't write the report")
    args = ap.parse_args()

    rows = run_cmc_check(save=not args.no_save)
    print("CMC status — measure-first (no flags changed)\n")
    print(f"{'source':34} {'status':14} notes")
    print("-" * 92)
    for r in rows:
        print(f"{r['source']:34} {_BADGE.get(r['status'], r['status']):14} {r['note']}")
    on = sum(1 for r in rows if r["status"] in ("LIVE", "ON"))
    print(f"\n{on} CMC layer(s) active. To enable more: docs/cmc_enablement.md "
          "(enable → SIM-validate → sign-off). Measure PnL effect: `make ab_regime`.")
    if not args.no_save:
        print(f"wrote: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
