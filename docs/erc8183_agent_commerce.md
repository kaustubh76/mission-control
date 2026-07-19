# ERC-8183 Agent Commerce — the agent monetizes its own edge (BNB AI Agent SDK)

**Best Use of BNB AI Agent SDK.** Most teams use the SDK for the table-stakes pillar — an ERC-8004
identity (mint + heartbeat). We use the SDK's **flagship, near-unused** surface, **ERC-8183 agentic
commerce** ("the first live ERC-8183 implementation for onchain AI agents"), to close a **two-sided
autonomous agent economy**:

- **Buy side (already live):** the agent pays for CMC data with **x402** (USDC on Base, per request).
- **Sell side (this build):** the agent offers its live **Market Regime Report** — regime score,
  deploy cap, momentum ranking, rationale — as a **paid ERC-8183 service** to other agents.

The same on-chain identity advertises **both** roles. One agent that pays for its inputs and
monetizes its outputs is the "most inventive integration" thesis — not plumbing bolted onto an LLM.

## What the agent sells (the deliverable)
`src/ictbot/agent/regime_report.py` packages the agent's *real* decision into a JSON deliverable —
it reuses the exact production decision path (`strategy.registry` + `momentum_allocator`,
`regime_score`, `rationale.explain`), so the report **is** what the agent trades on. It is
verifiably **CoinMarketCap intelligence**, carrying explicit provenance:

- **CMC Pro API:** 4h close matrix + Fear & Greed.
- **CMC Data-MCP + composed Skill:** `market_overview` (its `tools_used` + `risk_budget`) and the
  MCP-authoritative `basket_ta_health` (RSI/MACD/EMA) — surfaced in `cmc_sources`.

```json
{ "schema":"cmc-regime-report/v1", "status":"ok", "regime_score":0.35, "deploy_cap":0.51,
  "momentum_ranking":["DOGE","AVAX","DOT","UNI"], "target_weights":{...}, "ta_health":...,
  "rationale":"CMC Fear & Greed is 25 (fear); risk-on 0.35 → cap 51% …",
  "cmc_sources":{"pro_api":[...],"mcp_skill":{"tools_used":[...]},"mcp_ta":"get_crypto_technical_analysis"} }
```

## Architecture
```
CMC (Pro API + Data-MCP + Skills)  ──>  regime_report.build_report()  ──>  deliverable JSON
                                                                              │
ERC-8004 identity (agentId 133085) ── advertises ──> ERC-8183 "commerce" endpoint
                                                                              │
 buyer agent: create_job ─> fund(token) ─> [provider auto-submits report] ─> settle (optimistic)
              (scripts/erc8183_demo_client.py)        (scripts/erc8183_serve.py)
```
- `src/ictbot/agent/commerce.py` — `ERC8183Client`/`ERC8183JobOps` over the **same identity wallet**;
  `on_job` builds the report, `serve()` runs `funded_job_watcher` (autonomous: poll FUNDED → submit).
- `scripts/erc8183_serve.py` — the unattended provider. `scripts/erc8183_demo_client.py` — a buyer.
- `src/ictbot/agent/identity.py` — advertises the `commerce` endpoint (`COMMERCE_CAPABILITIES`) so
  peers discover the service via `get_all_agents`.

## Security model
- **Signing** uses the **local** identity keystore (`AGENT_WALLET_PASSWORD`); the key never leaves
  the process or is logged. `commerce.available()` requires the password, so the provider never runs
  on the read-only (zero-secret) dashboard deploy.
- The **deliverable is public market analysis only** — no key, password, or path (unit-tested:
  `test_deliverable_carries_no_secret`). The jobs journal records only public job metadata.
- The ERC-8183 client signs only the job-lifecycle **contract calls** (create/fund/submit/settle) —
  not arbitrary EIP-712 typed data (that surface stays the x402 `SigningPolicy`-guarded path).

## Status (2026-06-18) — LIVE on BSC MAINNET, real jobs served
- ✅ Deliverable builder reuses the real decision path; CMC Pro API + MCP + Skills provenance; **no
  secrets** (unit tests pass).
- ✅ Identity advertises the ERC-8183 `commerce` endpoint when enabled.
- ✅ **Two real jobs served end-to-end on bsc-mainnet** by provider/identity wallet
  `0xEb7bF36aab4912c955474206EF0b835170389655`, bought by a **distinct** buyer agent
  `0x9e4AF0547B166efE14b377108d5856FdfB3B74d6` (genuinely agent-to-agent) — each `create → fund (0.1 U)
  → submit signed deliverable on-chain → IPFS-pinned report`. Ledger:
  [`data/journal/commerce_jobs.jsonl`](../data/journal/commerce_jobs.jsonl).

  | Job | Submit tx (BSC) | Deliverable (IPFS) | Settle |
  |---|---|---|---|
  | **25741** | `0x73546c6d…02d0f669` | `ipfs://QmTXDHUPYTTFbqptJvjAsNAVPnCfaNVso9CmHpjYBb5cnp` | deferred → finalizes after dispute window |
  | **26506** | `0xcc13c3e6…d51accd98c` | `ipfs://Qmd6hqiF4QRnLEnw282SmACc5RYwbSBBDn4xdHzZojFRoY` | deferred → finalizes after dispute window |

### Settlement is optimistic — `SETTLE_DEFERRED` is expected, not a failure
The kernel uses an **optimistic dispute window**: `settle()` reverts with `NotDecided()` (selector
**`0x17be5b7b`**) until the ~7-day window after submission elapses. So `create_and_serve_job` submits
the deliverable on-chain immediately and leaves the job **`SETTLE_DEFERRED`** — the on-chain *delivery*
is already done and verifiable; only the escrow release waits out the window. `commerce.settle_pending_jobs()`
(operator-local, buyer-signed) finalizes each job once its window closes (the two jobs above submitted
2026-06-17 → settle ≈ 2026-06-24). `scripts/settle_commerce.sh` runs this on a window-scoped cron so the
loop closes on-chain hands-off — a finalized `SETTLE` row then lands in the ledger.

## Run it
```bash
# MAINNET (LIVE) — real agentId 133085, real payment token "U"; keyed MegaFuel paymaster or direct-gas.
# Jobs are created from the dashboard "Create Job" button (buyer-signed) or the buyer client below.
ERC8183_NETWORK=bsc-mainnet ERC8183_ENABLED=true python scripts/erc8183_serve.py --check
bash scripts/settle_commerce.sh        # finalize served jobs whose dispute window has closed

# TESTNET (gasless) — the SDK's public MegaFuel; buyer funds a faucet "U" balance first.
ERC8183_ENABLED=true python scripts/erc8183_serve.py             # autonomous provider loop
CLIENT_WALLET_PASSWORD=demo python scripts/erc8183_demo_client.py --provider 0x<agent-address>
```
