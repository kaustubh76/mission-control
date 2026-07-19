#!/usr/bin/env python3
"""
Probe the CoinMarketCap **Agent Hub** end-to-end — the live "Best Use of CMC" evidence.

Confirms, against the real endpoints:
  1. Data MCP  — `tools/list` (the 12 tools) + a sample pre-computed TA call,
  2. Skills    — runs the composed `market_overview()` pipeline (agent-ready regime read),
  3. x402      — the 402 challenge on the pay-per-call endpoints (quotes/latest, /x402/mcp),
captures the exact wire shapes, and writes data/journal/cmc_agent_hub_probe.json.

Read-only: the MCP/skill calls use the Startup key; the x402 step only fetches the unpaid
402 CHALLENGE (no settlement, no spend). Run:  PYTHONPATH=src python scripts/probe_agent_hub.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ictbot.data import cmc_agent_hub as hub  # noqa: E402
from ictbot.data import x402_cmc  # noqa: E402
from ictbot.settings import JOURNAL_DIR, settings  # noqa: E402

X402_ENDPOINTS = [
    "/x402/v3/cryptocurrency/quotes/latest",
    "/x402/v1/dex/search",
    "/x402/mcp",
]


def main() -> int:
    if not settings.cmc_api_key:
        print("No CMC_API_KEY (set it in .env). Aborting.")
        return 1
    # The MCP layer is flag-gated; force it on for the probe regardless of .env.
    settings.cmc_mcp_enabled = True
    report: dict = {"mcp": {}, "skill": {}, "x402": {}}

    print("=== 1. Data MCP — tools/list ===")
    listing = hub._rpc("tools/list", {})
    tool_defs = (((listing or {}).get("result") or {}).get("tools") or [])
    tools = [t["name"] for t in tool_defs]
    report["mcp"]["tools"] = tools
    report["mcp"]["schemas"] = {t["name"]: t.get("inputSchema") for t in tool_defs}
    print(f"  {'OK ' if tools else 'XX '} {len(tools)} tools: {', '.join(tools) or '(none)'}")

    print("\n=== 1b. Skills Marketplace — endpoint discovery (honesty check) ===")
    # CMC's Skills Marketplace (coinmarketcap.com/api/skills-marketplace) is an agent-side
    # router, NOT callable JSON-RPC tools. Probe the obvious endpoints to PROVE that, so the
    # composed market_overview() is labeled skill_source="composed" on evidence, not guess.
    import urllib.request as _u
    base = settings.cmc_mcp_url.rsplit("/", 1)[0]
    candidates = [f"{base}/skills", f"{base}/skills/mcp", f"{base}/skills-marketplace",
                  f"{base}/skills-marketplace/mcp"]
    disc = {}
    for url in candidates:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
        req = _u.Request(url, data=body, method="POST",
                         headers={"Content-Type": "application/json",
                                  "X-CMC-MCP-API-KEY": settings.cmc_api_key})
        try:
            with _u.urlopen(req, timeout=15) as r:
                code = r.status
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None) or type(e).__name__
        disc[url] = code
        print(f"  {str(code):>5}  {url}")
    report["mcp"]["skills_marketplace_discovery"] = disc
    callable_skill = any(c == 200 for c in disc.values())
    print(f"  -> callable marketplace skill endpoint: {'FOUND' if callable_skill else 'NONE'} "
          f"(skill_source={'cmc-marketplace' if callable_skill else 'composed'})")

    print("\n=== 2. Pre-computed TA (get_crypto_technical_analysis, BNB) ===")
    ta = hub.technical_analysis("BNB")
    report["mcp"]["sample_ta_bnb"] = ta
    if ta:
        print(f"  OK  rsi14={ta.get('rsi', {}).get('rsi14')}  "
              f"macd_hist={ta.get('macd', {}).get('histogram')}  "
              f"ema30={ta.get('moving_averages', {}).get('exponential_moving_average_30_day')}")
    else:
        print("  XX  no TA payload")

    print("\n=== 3. Composed market-overview skill — market_overview() pipeline ===")
    mo = hub.market_overview()
    report["skill"]["market_overview"] = mo
    report["skill"]["cmc_ids_verified"] = hub.verify_cmc_ids()
    if mo:
        print(f"  OK  {mo['headline']}")
        print(f"      skill_source={mo.get('skill_source')}  risk_budget={mo['risk_budget']}")
        print(f"      tools_used={mo['tools_used']}")
        print(f"      narratives={mo['narratives']}")
    else:
        print("  XX  skill returned None")
    ids = report["skill"]["cmc_ids_verified"]
    print(f"      CMC_IDS resolution: {ids['matched']}/{ids['total']} verified "
          f"{'· mismatches=' + str(ids['mismatches']) if ids['mismatches'] else ''}")

    print("\n=== 4. x402 pay-per-call — 402 challenge (no settlement) ===")
    for path in X402_ENDPOINTS:
        params = {"id": "1"} if "quotes" in path else ({"q": "bnb"} if "dex" in path else None)
        ch = x402_cmc.fetch_challenge(path, params)
        accept = x402_cmc.pick_accept(ch) if ch else None
        payable = accept is not None
        report["x402"][path] = {"challenge": bool(ch), "payable_base_usdc": payable,
                                "amount_units": (accept or {}).get("amount"),
                                "x402_version": (ch or {}).get("x402Version")}
        print(f"  {'OK ' if payable else '-- '} {path:46} "
              f"{'payable $'+str(int((accept or {}).get('amount', 0))/1e6) if payable else 'no Base-USDC accept'}")

    dest = JOURNAL_DIR / "cmc_agent_hub_probe.json"
    dest.write_text(json.dumps(report, indent=2, default=str))
    n_x402 = sum(1 for v in report["x402"].values() if v["payable_base_usdc"])
    print(f"\nwrote {dest}")
    print(f"summary: MCP tools={len(tools)} · skill={'OK' if mo else 'FAIL'} · "
          f"x402-payable endpoints={n_x402}/{len(X402_ENDPOINTS)} · "
          f"MCP calls this run={hub.telemetry()['calls']}")
    print("Data provided by CoinMarketCap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
