# vlayer Grant Application — Draft

> Paste-ready answers for <https://www.vlayer.xyz/grants>. Fill `<...>` before submitting.
> Grant model: $1K–$10K, retroactive/milestone-based, rolling, decision in ~2 weeks.

## Project

**Name:** Mission Control — Verifiable Agent Commerce
**One-liner:** An autonomous trading agent that *sells* its live market analysis to other agents —
and uses vlayer Web Proofs to make that analysis' data provenance cryptographically verifiable
on-chain. The analysis is built from **free, keyless public data** (Binance · alternative.me ·
DexScreener · CoinGecko); vlayer proves those inputs are genuine.

**Links**
- Repo: https://github.com/kaustubh76/mission-control
- Live dashboard: https://mission-control-vlayer.onrender.com
- Read-only API: https://mission-control-api-iez5.onrender.com
- ERC-8004 identity: agentId 133085 (BSC)
- ERC-8183 jobs (mainnet): 25741, 26506
- Architecture diagram (vlayer trust layer = band ④, row 2): [`docs/architecture.svg`](../architecture.svg)
- Integration plan: [`docs/vlayer/INTEGRATION_PLAN.md`](INTEGRATION_PLAN.md)
- vlayer attestation tx (M1): `<Optimism Sepolia tx hash — fill after first prove>`

## The problem vlayer solves for us

Our agent **sells** a live *Market Regime Report* to peer agents (ERC-8183, real jobs settled on BSC
mainnet). The report is built from free public market data (Binance candles, alternative.me Fear &
Greed, DexScreener DEX signals, CoinGecko macro). But its provenance — "these numbers came from those
sources" — is currently just a **claim the buyer must trust**. Nothing stops a malicious agent from
selling fabricated "market data." (The agent originally also *bought* its data via x402 micropayments
on Base — the BNB-hackathon build; it now runs on the free stack above.)

## What we build with vlayer

**Web Proofs (zkTLS)** to prove, on-chain, that a specific market-data HTTPS response was genuinely
served by its real source, and bind that proof to the exact report we sell. We lead with the **free
alternative.me Fear & Greed** endpoint (`api.alternative.me` — keyless; the proof still attests the
response genuinely came from that host over TLS). A `RegimeProver` verifies the web proof and
extracts the Fear & Greed value; a `RegimeVerifier` (`onlyVerified`) records an on-chain
attestation keyed by the report hash. The sold ERC-8183 deliverable then carries a buyer-checkable
`provenance_proof`.

**Stretch — Time Travel:** make our headline survival claim (13.2% worst-week drawdown vs the 30%
DQ line) verifiable against historical on-chain NAV heartbeats.

## Answers to the six evaluation criteria

1. **Team capability & commitment.** Shipped a live, self-custody mainnet agent: ~38k LOC Python,
   1,600+ tests, real x402 settlements on Base, real ERC-8183 jobs on BSC, deployed dashboard +
   API. Solo/small-team, actively building.
2. **Depth of vlayer integration & impact.** Load-bearing, not cosmetic: vlayer becomes the *trust
   layer* of an agent-to-agent data economy. Without it, sold analysis is unverifiable; with it,
   provenance is cryptographic. Directly hardens the core product mechanic.
3. **Sustainability & monetization.** The agent already earns on-chain fees selling reports
   (ERC-8183). Verifiable provenance is a paid-product upgrade — "verified market intelligence" is
   worth more than "trust-me intelligence." Reusable pattern → repeatable revenue.
4. **Originality.** A self-custody AI agent that sells market analysis and **proves its free-data
   provenance on-chain** (its inputs are genuinely from the named public sources) is a novel
   composition — "verifiable agent commerce." Runs entirely keyless.
5. **Technical feasibility & roadmap.** Concrete M0–M4 milestones (see INTEGRATION_PLAN.md), EVM +
   Solidity already in the stack, prover/verifier scaffolded, closest template identified
   (`kraken-web-proof`). Low-risk path to a testnet attestation.
6. **Ecosystem value.** Produces a reusable open pattern — "on-chain data-provenance attestations
   for AI agents" — that any ERC-8004/ERC-8183 agent can adopt. A template contribution back to the
   vlayer + agent-commerce ecosystems.

## Ask & milestones

- **Amount requested:** $5,000–$8,000 (sized to M1–M3 + docs/demo).
- **Milestones:** M1 Web Proof e2e on Optimism Sepolia → M2 bind proof to sold report → M3
  (stretch) Time Travel track-record proof → M4 dashboard "Verified by vlayer" + demo + docs.

## Data sources (all free, keyless)

Binance klines (4h + daily candles) · alternative.me (Fear & Greed) · DexScreener (DEX price ·
liquidity · volume) · CoinGecko (BTC dominance · mktcap). No API key, no account. See
[`docs/FREE_DATA_SOURCES.md`](../FREE_DATA_SOURCES.md).

## What's already done before applying (tangible progress)

Live mainnet product + a full **free-data migration** (the agent runs keyless; the dashboard's live
"Market Data Hub" shows the free stack) + this vlayer scaffold (prover/verifier contracts, TS prove
script, Python provenance bridge) building locally, and the trust layer is already drawn into the
system architecture ([`docs/architecture.svg`](../architecture.svg), band ④ row 2) so a reviewer sees
exactly where the Web Proof binds the sold report. First testnet attestation tx is the concrete
vlayer artifact we show at review.
</content>
