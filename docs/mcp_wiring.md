# MCP wiring — how the bot talks to CMC, and how each skill pairs with it

> **The CMC MCP is wired and the skills consume it live.** Verify any time with `make mcp_check`.
> Generated 2026-06-14. Companion to [cmc_enablement.md](cmc_enablement.md) (the measured
> enable/disable evidence) and `make cmc_check` (the per-source status).

## TL;DR — `make mcp_check` proves it

```
MCP LIVE ✅ — 12/12 tools, sample call OK, 10 skill(s) paired
```

Every skill is **enabled**, its CMC MCP tool is **live**, and the production journal proves it was
**consumed** last tick (`ta_source: "cmc+skill"`, `cmc_intel_used: true`, `cmc_skill.tools_used` lists the
tools called). The earlier impression that "MCP isn't wired" came from `cmc_check` rendering the MCP row as
a raw flag (`ON`) instead of a live probe — now fixed: it reads live-verified `LIVE (12/12 tools)`.

## Transport — what "MCP" means here

The bot ([`src/ictbot/data/cmc_agent_hub.py`](../src/ictbot/data/cmc_agent_hub.py)) calls CMC's **hosted
MCP server** over **HTTP JSON-RPC**:

- `POST https://mcp.coinmarketcap.com/mcp` (`CMC_MCP_URL`), auth header `X-CMC-MCP-API-KEY: <CMC_API_KEY>`.
- `_rpc("tools/list", {})` enumerates the **12 callable Data-MCP tools**; `_rpc("tools/call", {name,args})`
  invokes one. TTL-cached, usage-journaled, never raises into a tick (degrades to `None` → local fallback).
- **Honest label:** this is CMC's HTTP-JSON-RPC endpoint *branded* "MCP" — there is **no `mcp` Python SDK /
  stdio / SSE** here (a hand-rolled `urllib` JSON-RPC client). It works; it's just not the literal MCP
  protocol transport. CMC's *Skills Marketplace* is a separate product with no callable tool endpoint
  (`/skills*` → 404), so `market_overview()` is **our own composed skill** over the 12 Data-MCP tools.
- **Crypto.com's MCP** (the `get_candlestick`/`get_ticker` server you see in claude.ai) is a **session
  tool**, reachable only by Claude in-session — the standalone cron bot **cannot** reach it (zero code
  references). Not wired, by design; the bot's market data is Binance candles + CMC intel.

## Skill ↔ MCP-tool pairing (verified by `make mcp_check`)

| Skill | MCP tool(s) | Flag | Verdict |
|---|---|---|:--:|
| Basket TA → deploy cap | `get_crypto_technical_analysis` | `ALLOC_TA_ENABLED` | ✅ PAIRED |
| Token TA → ranking tilt | `get_crypto_technical_analysis` | `ALLOC_TA_ENABLED` (+ `_W_RANK>0`) | ✅ PAIRED |
| Market-overview skill | `get_crypto_technical_analysis`, `get_global_metrics_latest`, `trending_crypto_narratives` | `CMC_SKILL_REGIME` | ✅ PAIRED |
| Derivatives stress | `get_global_crypto_derivatives_metrics` | `CMC_DERIV_BRAKE` | ✅ PAIRED |
| Macro guard | `get_upcoming_macro_events` | `CMC_MACRO_GUARD` | ✅ PAIRED |
| News brake | `get_crypto_latest_news` | `CMC_NEWS_ENABLED` | ✅ PAIRED |
| Market-cap TA | `get_crypto_marketcap_technical_analysis` | `CMC_MKTCAP_TA` | ✅ PAIRED |
| Quotes cross-check | `get_crypto_quotes_latest` | `CMC_QUOTES_XCHECK` | ✅ PAIRED |
| Regime intel (dominance/mktcap) | _Pro API_ `/v1/global-metrics` | `CMC_INTEL_ENABLED` | ✅ PAIRED |
| Fear & Greed | _Pro API_ `/v3/fear-and-greed` | _(needs key)_ | ✅ PAIRED |

**Verdicts:** ✅ PAIRED = enabled + tool live + consumed last tick · 🟢 LIVE = enabled + tool live ·
🟡 READY = tool live but flag off · ⚠️ DEGRADED = enabled but tool down → **local fallback** (the bot never
hard-fails; `technicals.py` recomputes the same signal from 4h candles) · ⚪ OFF = flag off / no key.

## Verify / probe

```bash
make mcp_check        # live: tools/list + sample call + per-skill pairing → data/reports/mcp_status.md
make probe_agent_hub  # deeper one-shot probe (tool schemas, sample TA, market_overview, x402 challenge)
make cmc_check        # the per-source status (MCP row now live-verified)
```

`make mcp_check` is **read-only** (one `tools/list` + one cheap `tools/call` — a few CMC credits; no trades,
no flag changes). If the MCP is ever down, the skills degrade to local compute and the report shows
`⚠️ DEGRADED` — that's the safety net, not a failure. The x402 layer stays enrichment-only (journaled,
never drives a trade).
