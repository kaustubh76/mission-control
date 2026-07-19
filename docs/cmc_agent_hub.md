# Best Use of the CoinMarketCap AI Agent Hub

**The one-liner:** We made the CMC AI Agent Hub the **decision brain** of a live, autonomous,
100%-CMC-native trading agent — where CMC's pre-computed intelligence doesn't get *displayed*, it
**sizes real capital every tick**, the agent **pays CMC on-chain** for premium data, and every
decision is **provenance-stamped and reproducible**. Depth, not a demo.

The Agent Hub wires live CMC data into agents through MCP, x402, the CMC CLI, IDE integrations, and
pre-built Skills. Here is exactly how this project uses each surface — with the files, tools and
endpoints a judge can verify.

---

## 1. MCP — CMC's Data-MCP is the agent's regime brain

We call CMC's hosted MCP server (`https://mcp.coinmarketcap.com/mcp`, auth `X-CMC-MCP-API-KEY`) from
[`src/ictbot/data/cmc_agent_hub.py`](../src/ictbot/data/cmc_agent_hub.py) and wire **all 12 Data-MCP
tools** into *atomic trading levers* — the actual allocation, not a chart:

| MCP tool | What it drives in the live decision |
|---|---|
| `get_crypto_technical_analysis` | per-token RSI/MACD/EMA breadth → **deploy-cap risk budget** *and* a **per-token ranking tilt** (which coins we hold) |
| `get_global_metrics_latest` | Fear & Greed + BTC dominance + mktcap pulse → **regime score** scaling deployment 35–80% |
| `get_global_crypto_derivatives_metrics` | OI + funding stress → **multiplicative cap brake** |
| `get_upcoming_macro_events` | de-risk **cap haircut into FOMC/CPI** |
| `get_crypto_latest_news` | negative-headline **circuit-breaker** |
| `get_crypto_marketcap_technical_analysis` | global RSI/MACD → extra regime term |
| `trending_crypto_narratives` | sector/narrative context in the agent's rationale |
| `get_crypto_quotes_latest` | price cross-check + CMC-ID resolution proof |
| `get_crypto_info`, `get_crypto_metrics`, `search_cryptos`, `search_crypto_info` | metadata + discovery |

Each tick journals `cmc_skill.tools_used` — the exact MCP tools that shaped *that* decision —
alongside `ta_source` (`cmc` / `cmc+skill` / `local`). Live health is proven read-only by
[`scripts/mcp_check.py`](../scripts/mcp_check.py) (`make mcp_check` → PAIRED/LIVE/READY per skill) and
per-tool call counts in `data/journal/cmc_mcp_usage.json`.

## 2. x402 — the agent autonomously pays CMC for premium data, on-chain

Not a mocked 402. From [`src/ictbot/data/x402_cmc.py`](../src/ictbot/data/x402_cmc.py) the agent holds
a **Base wallet** and settles **real EIP-3009 `TransferWithAuthorization` USDC micropayments** to
CMC's x402 endpoints (`/x402/v1/dex/search`), through a hardened `X402Signer` with **per-call +
session USDC budget caps** and **recipient-match enforcement**, writing an on-chain **receipts
ledger** at `data/x402/receipts.json`. It buys live DEX liquidity for its top holding and journals it
(`x402_attempted` / `x402_failed` / `x402_dex`). The 402-challenge → sign → resend flow is confirmed
against the live mainnet endpoint. Pillar-1, end-to-end.

## 3. Pre-built Skills — a composed market-overview skill, with honest labeling

CMC's Skills Marketplace is not yet exposed as callable JSON-RPC — we probe `/skills`,
`/skills/mcp`, `/skills-marketplace*` and they 404 (recorded by
[`scripts/probe_agent_hub.py`](../scripts/probe_agent_hub.py) →
`data/journal/cmc_agent_hub_probe.json`). Rather than fake a marketplace call, we **composed our own
skill** on the Data-MCP tools: `market_overview()` stitches 8 CMC tools into a single numeric **risk
budget ∈ [0,1]** that modulates the deploy cap, and we label it `skill_source="composed"` with the
probe as proof. When CMC ships callable skills it is a one-line swap to `skill_source="cmc-marketplace"`.
A working composition + intellectual honesty beats a faked call.

## 4. The full CMC surface, fused

Beyond the Agent Hub we run **three more CMC layers**, all feeding one agent:
- **Pro REST API** ([`cmc.py`](../src/ictbot/data/cmc.py), [`cmc_intel.py`](../src/ictbot/data/cmc_intel.py)):
  quotes/latest, fear-and-greed, global-metrics, listings/latest, cryptocurrency/info — through a
  hardened client with rate-limit, credit budget, retry and TTL cache.
- **DEX API** ([`cmc_onchain.py`](../src/ictbot/data/cmc_onchain.py)): `/v4/dex/spot-pairs/latest`
  (pool discovery) + `/v4/dex/pairs/quotes/latest` (per-pool liquidity depth + 24h volume).
- **Pro WebSocket** ([`scripts/cmc_stream.py`](../scripts/cmc_stream.py)): the full multi-channel
  harvest — `market@crypto_latest_price` (full payload: price, market cap, supply, multi-window %
  change) **plus five `onchain@*` channels** (`token_metric`, `holders_metrics`, `liquidity_event`,
  `token_agg_event`, `transaction`) feeding live buy/sell flow, holder concentration
  and whale-flow into the allocation overlays.

Using MCP + x402 + REST + DEX + WebSocket **together** is rare — most teams touch one.

## 5. 100% CMC-native + auditable

The contest arm runs behind a **zero-CEX firewall**: token selection on CMC's own 4h candles
(accumulated from the CMC WebSocket), sizing on CMC Fear & Greed + MCP technicals + the composed
Skill, execution-pricing on CMC quotes. Every `REBALANCE` row stamps its provenance —
`candle_source`, `quote_source`, `ta_source`, `cmc_skill.tools_used`, `x402_*`, `cmc_credits_today` —
and three read-only probe scripts ([`probe_agent_hub`](../scripts/probe_agent_hub.py),
[`mcp_check`](../scripts/mcp_check.py), [`cmc_check`](../scripts/cmc_check.py)) prove the wiring is
live. Slideware can't do that.

## On CLI / IDE (honest)

The CMC CLI and IDE integrations are **developer-experience** surfaces; our "agent" is the unattended,
cron-driven trader itself, so we integrate where an autonomous agent actually consumes the Hub —
**MCP + x402 + Skills**. We do not claim CLI or IDE use we don't have. They are a natural next
surface, not the core of a headless agent.

## Verified live (2026-06-16) — `make mcp_check` · `make probe_agent_hub` · `make cmc_check`

Not claims — generated, read-only evidence on file:

- **MCP: 12/12 Data-MCP tools live**, sample call OK; **10/10 skills PAIRED** (enabled + live + used)
  — Basket-TA→cap, Token-TA→rank, market-overview, derivatives, macro, news, mktcap-TA, quotes,
  regime-intel, Fear&Greed (`data/reports/mcp_status.md`).
- **Composed skill runs live**: `risk_budget=0.40` (neutral), TA breadth 7/8 bullish-MACD, F&G 25,
  derivatives stress 14%, **macro guard −15% into a Fed rate decision**, 8 `tools_used`, CMC_IDS 8/8
  verified (`data/journal/cmc_agent_hub_probe.json`).
- **Skills-Marketplace honesty check**: `/skills*` → **404** on all four candidates → `skill_source
  = "composed"`, recorded, not assumed.
- **x402 is real**: 2/3 endpoints payable ($0.01), and `data/x402/receipts.json` holds **20 on-chain
  `status:"settled"` Base-USDC receipts** — the agent has actually paid CMC.
- **Per-tick provenance** is auditable in the journal (`candle_source`, `quote_source`, `ta_source`,
  `cmc_skill.tools_used`, `x402_*`, `cmc_credits_today`); `make cmc_check` rolls up what flowed in.

## Why this is the best use

The Agent Hub here isn't decoration — it's the brain. CMC's pre-computed intelligence drives **which**
assets we hold, **how much** we deploy, and **when** we de-risk; the agent **pays CMC on-chain** for
premium data; and **every decision is provenance-stamped and reproducible** from probe scripts and
journals. Live capital, real payments, full audit trail — that's the Agent Hub used the way it was
meant to be.
