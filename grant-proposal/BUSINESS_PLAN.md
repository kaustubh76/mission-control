# Mission Control — vlayer Grant Business Plan

**Verifiable agent commerce.**

> Mission Control is an autonomous, self-custody trading agent that **sells its live market analysis
> to other agents** (ERC-8183) under one on-chain identity — built on free, keyless public data
> (Binance · alternative.me · DexScreener · CoinGecko). This grant funds the **vlayer Web Proof**
> layer that makes the sold analysis' data provenance **cryptographically verifiable on-chain** —
> turning "trust me" agent commerce into "prove it" agent commerce.

*vlayer Grants submission (rolling, milestone-based, $1K–$10K per project). Every figure resolves to
one row of the appendix — reviewers are invited to check. Last updated: 2026-07-19.*

---

## Executive Summary

**The ask.** A **$5,000–$8,000** vlayer grant (retroactive/milestone-based), sized to milestones
**M1–M4** below — from the first on-chain attestation to a demoed "Verified by vlayer" surface on a
live mainnet product.

**What exists (tangible progress — vlayer's bar).** Mission Control is a live, self-custody agent with
**381 verifiable agent transactions on mainnet** (83 self-signed swaps + 221 on-chain heartbeats + 75
x402 micropayments + **2 ERC-8183 commerce jobs**), a full **free-data migration** (the agent runs
keyless — Binance candles, alternative.me Fear & Greed, DexScreener DEX signals, CoinGecko macro), and
a **complete vlayer integration scaffold**: a `Prover`/`Verifier` contract pair, a TypeScript prove
script, a read-only Python provenance bridge, and a live **"Verified by vlayer"** dashboard panel — all
building and tested locally today.

**The gap vlayer closes.** When a peer agent buys Mission Control's **Market Regime Report** over
ERC-8183, the report's data provenance ("these numbers came from those sources") is a **claim the
buyer must trust**. Nothing stops a malicious agent from selling fabricated "market data." vlayer
**Web Proofs (zkTLS)** prove, on-chain, that a specific market-data HTTPS response was genuinely served
by its real host — **without revealing any API key** — and bind that proof to the exact report the
agent sells. We lead with the **free alternative.me Fear & Greed** endpoint (keyless), with a
DexScreener price proof as the extension.

**Why this is a strong vlayer fit.** It is **load-bearing, not cosmetic**: vlayer becomes the *trust
layer* of a two-sided agent economy that already settles real jobs on mainnet. It is **novel** — a
self-custody agent that both earns from its outputs (ERC-8183) and **proves its inputs' provenance**
on-chain. It has a **monetization path** (verified intelligence is a paid-product upgrade over
trust-me intelligence). And it produces a **reusable pattern** — on-chain data-provenance attestations
any ERC-8004/ERC-8183 agent can adopt.

**Honest scope, stated plainly.** The integration is **built and dashboard-ready**, but the first
on-chain attestation is the next step (**M1**), gated only on installing the vlayer toolchain
(`vlayerup`). No attestation transaction exists yet; the panel renders a truthful "pending" state
until it does. The free-data migration **is** complete and verified (Python test suite green, live
dashboard on the free stack). This plan funds turning the scaffold into a live, demoed attestation.

---

## The Problem

Autonomous agents increasingly **buy and sell data and analysis to each other**. But agent-to-agent
commerce has no native trust layer:

| Today, when an agent sells analysis… | The buyer has no way to… |
|---|---|
| The deliverable carries a `data_provenance` **string** | …verify the data actually came from the named source |
| Settlement is on-chain (ERC-8183) | …check the *inputs* were genuine, only that a job settled |
| The seller could fabricate "market data" | …distinguish real intelligence from a convincing forgery |

We have not found a shipping agent-commerce product where the **buyer can cryptographically verify the
seller's data provenance on-chain**. That is exactly what vlayer Web Proofs add — and Mission Control
is a live, mainnet agent-commerce product ready to be the reference implementation.

---

## Product — the vlayer integration

| Layer | What it does | Status |
|---|---|---|
| **Free data stack** | Binance klines (4h+daily) · alternative.me F&G · DexScreener DEX signals · CoinGecko macro — keyless, no account | **Live** (migration complete, tests green) |
| **The sold product** | The agent's live **Market Regime Report**, sold to peer agents over **ERC-8183** | **Live** (2 mainnet jobs) |
| **vlayer Prover** | `RegimeProver.sol` — verifies a zkTLS Web Proof of the alternative.me F&G response; extracts the value; echoes `agent` + `reportHash` | **Scaffolded** |
| **vlayer Verifier** | `RegimeVerifier.sol` — `onlyVerified` gate → records an on-chain attestation (`latest[agent]`, optional `byReport[reportHash]`) | **Scaffolded** |
| **Provenance bridge** | `provenance.py` — read-only, key-free; attaches a buyer-checkable `provenance_proof` to the sold report | **Built + tested** |
| **Dashboard** | A branded **"Verified by vlayer"** panel + a live **Market Data Hub** showing the free stack | **Live** (pending → verified) |

**The load-bearing claim:** vlayer is the trust layer of the economy, not a bolt-on. Without it, the
sold analysis is unverifiable; with it, provenance is cryptographic and buyer-checkable on-chain.

---

## Business Model

**Revenue today: $0** — stated plainly (no external buyers of the report yet beyond the 2 seeded
mainnet jobs). The model vlayer strengthens:

| Line | Basis | vlayer's role |
|---|---|---|
| **ERC-8183 report sales** | Per-job USDC fee for the Market Regime Report | "**Verified** intelligence" commands more than "trust-me" intelligence |
| **Pay-per-use provenance** | The `provenance_proof` pointer travels with every sold report | A reusable, paid verifiability upgrade — repeatable across products |
| **Ecosystem pattern** | Open on-chain data-provenance attestation pattern | Reusable by any ERC-8004/ERC-8183 agent → compounding ecosystem value |

Agentic commerce is not yet a revenue line — it is the **distribution mechanism**. vlayer makes each
sold output *verifiable*, which is the feature that converts trust-gated buyers.

---

## Why vlayer (feature fit)

- **Web Proofs / zkTLS (lead).** Prove the free alternative.me F&G response on-chain (keyless); bind
  it to the sold report. Robust integer payload → clean extract, no fragile parsing.
- **Time Travel (stretch, M3).** Make the agent's headline survival claim (**13.2% worst-week
  drawdown** vs a 30% line) verifiable against historical on-chain NAV heartbeats.
- **Deliberately out of scope:** Email Proofs (no email-gated flow) and Teleport (no cross-chain
  contract-execution need yet).

---

## Competition

| Capability | Trust-me data feeds | Oracle-bridged data | Agent-token projects | **Mission Control + vlayer** |
|---|---|---|---|---|
| Sells analysis agent-to-agent (on-chain) | No | No | Rarely shipped | **Yes — 2 ERC-8183 mainnet jobs** |
| Buyer-verifiable data provenance | No | Trusted third party | No | **Yes — zkTLS Web Proof, on-chain** |
| Keyless / no secret to leak | Varies | No | Varies | **Yes — free public sources, no API key** |
| Live mainnet product | Varies | Varies | Narrative | **Yes — 381 verifiable agent txns** |

vlayer, Optimism, and the free data sources are rails we **integrate with**, not rivals — Mission
Control is a flagship consumer of vlayer's verifiable-data stack.

---

## Risks & Mitigations

| Risk | Status | Mitigation |
|---|---|---|
| **Attestation not yet live** | True — scaffold built, no tx yet | M1 = first Optimism Sepolia attestation; gated only on `vlayerup` install (the code path is written + unit-pinned) |
| **SDK/version drift** | Real — hand-written to vlayer's documented API | Reconcile once against `vlayer init --template kraken-web-proof`; contract logic is correct, only version strings shift (documented) |
| **Report-hash binding fragility** | Known | Simple path is agent-level (`latest[agent]`); the optional report-hash binding is documented with a single-source canonicalizer + a drift-guard test |
| **No demonstrated trading alpha** | True — net flow-adjusted PnL −$1.27 | Out of scope for this grant; vlayer funds **provenance verifiability**, not alpha. The proposal claims custody-safety + verifiability, not returns |

---

## Team

**Kaushtubh Agrawal — solo technical founder** (kaushtubhagrawal45@gmail.com). Shipped Mission Control
solo: ~37.9k Python LOC, 1,624 tests, a fully-unattended mainnet campaign, three agent-economy
protocol integrations (x402 / ERC-8004 / ERC-8183), a 30-component dashboard — plus the full vlayer
integration scaffold this grant funds to first attestation. Prize-winning at **BNB Hack** (Best Use of
Trust Wallet Agent Kit — sole winner of 317 projects) and three consecutive ETHGlobal events (a
**payments** product and an **AI-agent** product among them — the two halves of verifiable agent
commerce). Full track record in [PITCH_DECK.md](PITCH_DECK.md) Slide 6.

---

## The Ask & Milestones

- **Amount:** $5,000–$8,000 (sized to M1–M4).
- **M1** — Web Proof e2e on Optimism Sepolia (attestation tx hash). **M2** — bind the proof to a sold
  ERC-8183 report (`provenance_proof`). **M3** — (stretch) Time Travel drawdown proof. **M4** — the
  "Verified by vlayer" dashboard panel + demo video + docs. Full detail in [ROADMAP.md](ROADMAP.md).

---

## Appendix — Canonical Numbers & Verification Kit

*Single source of truth for every figure in this proposal. Blends the live Mission Control track
record (tangible progress) with the vlayer-integration specifics this grant funds.*

| Metric | Value | Verify |
|---|---|---|
| Verifiable agent transactions (BNB/Base mainnet) | **381** (83 swaps + 221 heartbeats + 75 x402 + 2 commerce) | [Trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215) |
| ERC-8183 commerce jobs (the product vlayer secures) | **2** — jobs 25741, 26506, IPFS-pinned | ipfs://QmTXDHUPYTTFbqptJvjAsNAVPnCfaNVso9CmHpjYBb5cnp |
| On-chain agent identity | ERC-8004 agentId **133085** | [Identity wallet](https://bscscan.com/address/0xEb7bF36aab4912c955474206EF0b835170389655) |
| Data stack (all free, keyless) | Binance · alternative.me · DexScreener · CoinGecko | [../docs/FREE_DATA_SOURCES.md](../docs/FREE_DATA_SOURCES.md) |
| vlayer proof target | alternative.me Fear & Greed (`api.alternative.me`, keyless) | [../vlayer/src/RegimeProver.sol](../vlayer/src/RegimeProver.sol) |
| vlayer scaffold | Prover + Verifier contracts, TS `prove.ts`, Python provenance bridge | [../vlayer/](../vlayer/) · [../docs/vlayer/INTEGRATION_PLAN.md](../docs/vlayer/INTEGRATION_PLAN.md) |
| Attestation chain (target) | Optimism Sepolia (vlayer default testnet) | [../vlayer/README.md](../vlayer/README.md) |
| Dashboard surface | "Verified by vlayer" panel + Market Data Hub (live, pending→verified) | live dashboard |
| Codebase / tests | ~37.9k Python LOC · 1,624 tests | [GitHub](https://github.com/kaustubh76/BNB) |
| Grant ask | **$5,000–$8,000**, milestones M1–M4 | [../docs/vlayer/GRANT_APPLICATION.md](../docs/vlayer/GRANT_APPLICATION.md) |

> **Honest scope.** The vlayer integration is **built and dashboard-ready** but **not yet attested
> on-chain** — M1 (the first Optimism Sepolia attestation) is the next step, gated only on the
> `vlayerup` toolchain. The "Verified by vlayer" panel renders a truthful *pending* state until then.
> The free-data migration is complete and verified. The 381 mainnet transactions, the 2 ERC-8183 jobs,
> and the ERC-8004 identity are real and explorer-checkable today; they are the tangible progress vlayer
> asks for. This grant funds turning the scaffold into a live, demoed, buyer-verifiable attestation.

**Verification kit:** [Trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215)
· [Agent identity 133085](https://bscscan.com/address/0xEb7bF36aab4912c955474206EF0b835170389655)
· [GitHub](https://github.com/kaustubh76/BNB) · [vlayer integration plan](../docs/vlayer/INTEGRATION_PLAN.md)
· [free data sources](../docs/FREE_DATA_SOURCES.md)
