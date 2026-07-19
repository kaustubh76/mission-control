# Mission Control — Verifiable Agent Commerce (vlayer)

> **An autonomous, self-custody trading agent that sells its live market analysis to other agents
> (ERC-8183) on free, keyless data — and uses vlayer Web Proofs to make that analysis' data provenance
> cryptographically verifiable on-chain. "Trust me" agent commerce becomes "prove it" agent commerce.**

**This deck is Mission Control's vlayer Grants submission.** The ask is a **$5,000–$8,000**
milestone-based grant to carry a **built, dashboard-ready** vlayer integration to its first on-chain
attestation and a demoed "Verified by vlayer" surface.

**How to read this deck.** Every mainnet number resolves to a block explorer; every vlayer artifact
resolves to a file in the repo. Each slide ends with a `Verify:` line, and every figure traces to the
single canonical [Appendix](#appendix---canonical-numbers--verification-kit). We wrote this expecting to
be audited; please do — including the one thing that is **not** yet on-chain (M1), which we state
plainly on Slide 5.

---

## Slide 1 — Mission Control: prove it, don't trust it

**Message**
- Mission Control is a **live, self-custody agent** that already runs a two-sided economy on one
  on-chain identity: it **sells** its Market Regime Report to peer agents (ERC-8183) and settles the
  jobs on mainnet — **381 verifiable agent transactions to date.**
- It now runs on **free, keyless data** (Binance · alternative.me · DexScreener · CoinGecko) — no API
  key, no account, nothing to leak.
- The grant funds one thing: the **vlayer Web Proof** layer that makes the sold report's data
  provenance **verifiable on-chain** — the missing trust layer of agent-to-agent commerce.

**Speaker notes**
I open with a live product, not a pitch. This agent already sells its market read to another agent and
settles the job on-chain — two real ERC-8183 jobs, IPFS-pinned, explorer-checkable. It runs entirely on
free public data now, so there is no secret in the loop. The one thing missing from agent commerce is
trust in the *inputs*: when my agent sells you a report, you can see the job settled, but you cannot
verify the numbers really came from the sources I claim. vlayer closes exactly that gap, and this deck
is my vlayer Grants submission to fund it.

**Visual**
Title with the Mission Control mark, a "381 verifiable agent transactions" counter, the four free-data
source logos, and a violet "Verified by vlayer" seal.

Verify: [Trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215) · [GitHub](https://github.com/kaustubh76/BNB) · [vlayer/](../vlayer/)

---

## Slide 2 — The trust gap in agent commerce

**Message**
- Autonomous agents increasingly **buy and sell data and analysis to each other** — but there is **no
  native way to verify the seller's data provenance**. The deliverable carries a `data_provenance`
  *string* the buyer must simply trust.
- Nothing stops a malicious agent from selling **fabricated "market data"** with a settled on-chain job
  behind it. Settlement proves a *payment*, not the *inputs*.
- **vlayer Web Proofs (zkTLS)** answer it: prove, on-chain, that a specific market-data HTTPS response
  was genuinely served by its real host — **without revealing any API key** — and bind that proof to the
  exact report sold. We lead with the **free alternative.me Fear & Greed** endpoint (keyless).

**Speaker notes**
Here is the empty quadrant. Agent commerce has settlement — ERC-8183 gives you create, fund, submit,
settle — but it has no provenance. A buyer sees the job closed; it cannot see whether the analysis was
built on real data or a convincing forgery. That is the trust gap, and it is exactly the shape of a
vlayer Web Proof: a zkTLS transcript that proves a named server returned a specific response, verifiable
on-chain, with no key exposed. We lead with alternative.me's Fear & Greed endpoint because it is free,
keyless, and its value is a clean integer — a robust first proof — then extend to a DexScreener price
proof.

**Visual**
Left: an ERC-8183 job lifecycle with a red "provenance?" gap over the deliverable. Right: the zkTLS Web
Proof closing it — notary → Prover → Verifier → on-chain attestation.

Verify: [../docs/vlayer/INTEGRATION_PLAN.md](../docs/vlayer/INTEGRATION_PLAN.md) · [../vlayer/src/RegimeProver.sol](../vlayer/src/RegimeProver.sol)

---

## Slide 3 — What's built: free-data agent + a complete vlayer scaffold

**Message**

| Shipped and verifiable today | Detail |
|---|---|
| Live mainnet agent | **381** verifiable agent transactions (83 swaps + 221 heartbeats + 75 x402 + **2** ERC-8183 jobs) |
| The product vlayer secures | The **Market Regime Report**, sold to a peer agent over ERC-8183 (IPFS-pinned) |
| Free-data migration (complete) | CoinMarketCap fully replaced — **Binance · alternative.me · DexScreener · CoinGecko**, keyless |
| vlayer contracts (scaffolded) | `RegimeProver` (verifies the alternative.me Web Proof) + `RegimeVerifier` (`onlyVerified` → attestation) |
| Prove script + Python bridge | `prove.ts` (notarize → prove → attest) + `provenance.py` (read-only, key-free `provenance_proof`) |
| Dashboard (live) | Branded **"Verified by vlayer"** panel + a live **Market Data Hub** on the free stack |

- The integration is **written to vlayer's documented API**, internally consistent (verifier args ==
  prover returns; Python ABI == contract, pinned by a drift-guard test), and **building/passing
  locally** — a scaffold ready to attest, not a sketch.

**Speaker notes**
This is the substance. The agent is real and on mainnet — 381 self-initiated on-chain actions, two of
them agent-to-agent sales of the exact report vlayer will secure. I migrated the whole data layer to
free, keyless sources, so there is no API key anywhere. And the vlayer integration is fully scaffolded:
a Prover that verifies an alternative.me Web Proof, a Verifier with the `onlyVerified` gate that records
the attestation, a TypeScript prove script, and a read-only Python bridge that hangs a buyer-checkable
`provenance_proof` on the sold report. It is consistent end to end — I even wrote a test that pins the
Python ABI to the Solidity source so they can't drift — and the dashboard already has the branded
"Verified by vlayer" panel, rendering a truthful pending state until the first attestation lands.

**Visual**
The five-box flow: free data → sold report (ERC-8183) → Prover → Verifier (Optimism Sepolia) →
"Verified by vlayer" panel, with the 381-counter beneath.

Verify: [../vlayer/](../vlayer/) · [../docs/FREE_DATA_SOURCES.md](../docs/FREE_DATA_SOURCES.md) · ipfs://QmTXDHUPYTTFbqptJvjAsNAVPnCfaNVso9CmHpjYBb5cnp

---

## Slide 4 — Why vlayer: the trust layer of the agent economy

**Message**
- **Load-bearing, not cosmetic.** Without vlayer, the sold analysis is unverifiable; with it,
  provenance is **cryptographic and buyer-checkable on-chain.** vlayer becomes the trust layer of a
  two-sided economy that already settles real jobs.
- **Novel composition.** A self-custody agent that both **earns from its outputs** (ERC-8183) and
  **proves its inputs' provenance** (vlayer) — "verifiable agent commerce." Runs entirely keyless.
- **Feature fit:** **Web Proofs / zkTLS** (lead — prove the free alternative.me F&G response);
  **Time Travel** (stretch — make the 13.2% drawdown survival claim verifiable against historical
  heartbeats). Email Proofs / Teleport are deliberately out of scope.
- **Ecosystem value:** a **reusable pattern** — on-chain data-provenance attestations any
  ERC-8004/ERC-8183 agent can adopt.

**Speaker notes**
Why is this a deep vlayer integration and not a bolt-on? Because it is the difference between the
product working and not: the whole value of selling analysis to another agent is that the buyer can
rely on it, and today they can't. vlayer is what makes the sold report trustworthy — it is load-bearing.
The composition is genuinely new: an agent that pays for nothing, keys nothing, earns from its outputs,
and proves its inputs on-chain. We lead with Web Proofs and hold Time Travel as a stretch to make our
survival claim verifiable too. And it generalizes — the attestation pattern is reusable by any agent on
the ERC-8004/8183 rails, which is the ecosystem contribution back to vlayer.

**Visual**
vlayer as a purple "trust layer" band under the agent-economy stack (identity / payments / commerce),
with the four vlayer features and Web Proofs highlighted.

Verify: [../docs/vlayer/GRANT_APPLICATION.md](../docs/vlayer/GRANT_APPLICATION.md) · [../vlayer/README.md](../vlayer/README.md)

---

## Slide 5 — The plan, and the one thing not yet on-chain

**Message**

| Milestone | Status | Deliverable / gate |
|---|---|---|
| **M0** — scaffold + free data | **Done** | Integration in-repo, consistent, tested; dashboard live |
| **M1** — Web Proof e2e | **Next** (needs `vlayerup`) | **Attestation tx hash** on Optimism Sepolia |
| **M2** — bind to sold report | After M1 | Served ERC-8183 job with a linked on-chain proof |
| **M3** — Time Travel (stretch) | Optional | Verified drawdown attestation |
| **M4** — dashboard + demo + docs | After M1–M2 | Live "Verified by vlayer" + demo video |

- **Stated plainly:** the integration is built and dashboard-ready, but **no attestation transaction
  exists yet.** M1 is gated only on installing the vlayer toolchain (`vlayerup`) — foundry + bun are
  already present, and the prove/verify code path is written and unit-pinned. The panel truthfully shows
  *pending* until then.
- This grant is **retroactive/milestone-based**: the scaffold, the completed free-data migration, and
  the live dashboard are the tangible progress; the grant funds the leap to a **live, demoed, verifiable
  attestation.**

**Speaker notes**
I am going to tell you the one thing that is not done, because the whole product is built on that kind of
honesty. There is no attestation transaction on-chain yet. Everything around it is finished — the
contracts, the prove script, the Python bridge, the dashboard panel, all consistent and tested — but the
first proof requires installing the vlayer toolchain, and that is M1. It is a short, well-understood step
once the template is diffed. The dashboard doesn't fake it; it shows pending until the proof lands. The
grant is milestone-based and partly retroactive, which fits exactly: you are funding a working scaffold
on a live mainnet product to reach its first real attestation and a demo — M1 through M4.

**Visual**
A five-gate ladder M0→M4 with M0 checked, M1 highlighted as "next — needs vlayerup," and a small
"pending → Verified" panel mockup.

Verify: [ROADMAP.md](ROADMAP.md) · [../vlayer/README.md](../vlayer/README.md) (Simple integration — happy path)

---

## Slide 6 — One founder, verified — and no shipping product does this

**Message**
- **Award — winner of Best Use of Trust Wallet Agent Kit at BNB Hack:** a **$2,000** special prize and
  the track's **sole winner** from **317 submitted projects** — for self-custody signing, autonomous
  execution, and native x402 as the heart of a hands-off trader.
- **Kaushtubh Agrawal** — solo technical founder. Prize-winning at three consecutive ETHGlobal events:
  **[DegenOS](https://ethglobal.com/showcase/degenos-i529f)** (Bangkok), **[PyPI](https://ethglobal.com/showcase/pypi-6wip3)**
  (New Delhi — Flow "Best Killer App", a **payments** product), **[Warriors AI-rena](https://ethglobal.com/showcase/warriors-ai-rena-f1owp)**
  (Cannes — 0G "Most Innovative Use", an **AI-agent** product). A payments product + an AI-agent product
  are the **two halves of verifiable agent commerce.**
- Shipped Mission Control solo: ~**37.9k** LOC, **1,624** tests, a fully-unattended mainnet campaign,
  three agent-economy protocols, a 30-component dashboard — **plus** the full vlayer scaffold this grant
  carries to attestation.
- The kill-line: **we have not found another shipping product that sells analysis agent-to-agent AND
  makes its data provenance buyer-verifiable on-chain.**

**Speaker notes**
Two questions a committee asks about a solo founder, answered with artifacts. Can one person ship on a
new stack, fast? Prizes at three consecutive ETHGlobal events, each on a stack learned on-site — and the
two winning domains, payments and AI agents, are exactly the two halves of this dapp. Can the work be
trusted without me? Open repo, 1,624 tests, and — specific to this grant — a vlayer integration that is
internally consistent and pinned by tests, documented down to a "simple integration" runbook. And on
competition: plenty of agents sell data, plenty of oracles bridge it, but I have not found one that sells
analysis agent-to-agent and lets the buyer verify the provenance on-chain. That is the row I am building.

**Visual**
Portrait beside a prize wall (BNB Hack + three ETHGlobal cards) and a "shipped solo" stat strip.

Verify: the links above · [GitHub](https://github.com/kaustubh76/BNB) · [Trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215)

---

## Slide 7 — The ask, and the audit invitation

**Message**
- **The ask:** a **$5,000–$8,000** vlayer grant (retroactive/milestone-based), sized to **M1–M4** —
  first attestation → bound to the sold report → dashboard + demo.
- **Why fund it:** deep, load-bearing vlayer integration on a **live mainnet agent-commerce product**;
  a novel "verifiable agent commerce" composition; a reusable on-chain provenance pattern for the
  ERC-8004/8183 ecosystem; and a solo founder who ships on new stacks under deadline.
- **Don't take my word for any of it:** [trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215)
  · [agent identity](https://bscscan.com/address/0xEb7bF36aab4912c955474206EF0b835170389655)
  · [GitHub](https://github.com/kaustubh76/BNB) · [vlayer/](../vlayer/) — and the appendix maps every
  figure to its proof. **Prove it, don't trust it.**

**Speaker notes**
The ask is small and milestone-shaped: five to eight thousand dollars to take a built integration on a
live product to its first on-chain attestation and a demo — M1 through M4. What you are funding is the
trust layer of an agent economy that already runs, from a founder who ships on new stacks fast. I will
close the way I opened: don't take my word for it. The wallet, the identity, the repo, and the vlayer
scaffold are all public, and if a number in this deck doesn't survive your audit, that is disqualifying.
I wrote it accepting that standard — because the whole product *is* that standard. Prove it, don't trust
it.

**Visual**
The ask ($5–8k, M1–M4) with a four-gate strip, and a QR grid (wallet, identity, GitHub, vlayer/) under
the "Prove it, don't trust it" tagline.

Verify: [BUSINESS_PLAN.md](BUSINESS_PLAN.md) · [ROADMAP.md](ROADMAP.md) · [../docs/vlayer/GRANT_APPLICATION.md](../docs/vlayer/GRANT_APPLICATION.md)

---

## Appendix — Canonical Numbers & Verification Kit

*Single source of truth for every figure in this deck. Blends the live Mission Control track record
(tangible progress) with the vlayer-integration specifics this grant funds.*

| Metric | Value | Verify |
|---|---|---|
| Verifiable agent transactions (BNB/Base mainnet) | **381** (83 swaps + 221 heartbeats + 75 x402 + 2 commerce) | [Trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215) |
| ERC-8183 commerce jobs (the product vlayer secures) | **2** — jobs 25741, 26506, IPFS-pinned | ipfs://QmTXDHUPYTTFbqptJvjAsNAVPnCfaNVso9CmHpjYBb5cnp · ipfs://Qmd6hqiF4QRnLEnw282SmACc5RYwbSBBDn4xdHzZojFRoY |
| On-chain agent identity | ERC-8004 agentId **133085** | [Identity wallet](https://bscscan.com/address/0xEb7bF36aab4912c955474206EF0b835170389655) |
| Data stack (all free, keyless) | Binance · alternative.me · DexScreener · CoinGecko | [../docs/FREE_DATA_SOURCES.md](../docs/FREE_DATA_SOURCES.md) |
| vlayer proof target | alternative.me Fear & Greed (`api.alternative.me`, keyless) | [../vlayer/src/RegimeProver.sol](../vlayer/src/RegimeProver.sol) |
| vlayer scaffold | Prover + Verifier + TS `prove.ts` + Python provenance bridge | [../vlayer/](../vlayer/) · [../docs/vlayer/INTEGRATION_PLAN.md](../docs/vlayer/INTEGRATION_PLAN.md) |
| Attestation chain (target) | Optimism Sepolia (vlayer default testnet) | [../vlayer/README.md](../vlayer/README.md) |
| Dashboard surface | "Verified by vlayer" panel + Market Data Hub (live; pending → verified) | live dashboard |
| Codebase / tests | ~37.9k Python LOC · 1,624 tests | [GitHub](https://github.com/kaustubh76/BNB) |
| Founder recognition | BNB Hack — Best Use of TWAK (sole winner / 317 projects); 3× ETHGlobal | Slide 6 links |
| Grant ask | **$5,000–$8,000**, milestones M1–M4 | [../docs/vlayer/GRANT_APPLICATION.md](../docs/vlayer/GRANT_APPLICATION.md) |

> **Honest scope.** The vlayer integration is **built and dashboard-ready** but **not yet attested
> on-chain** — M1 (the first Optimism Sepolia attestation) is the next step, gated only on the
> `vlayerup` toolchain. The "Verified by vlayer" panel renders a truthful *pending* state until then.
> The free-data migration is complete and verified. The 381 mainnet transactions, the 2 ERC-8183 jobs,
> and the ERC-8004 identity are real and explorer-checkable today. This grant funds turning the scaffold
> into a live, demoed, buyer-verifiable attestation — not alpha, which is out of scope.

**Verification kit:** [Trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215)
· [Agent identity 133085](https://bscscan.com/address/0xEb7bF36aab4912c955474206EF0b835170389655)
· [GitHub](https://github.com/kaustubh76/BNB) · [vlayer integration](../vlayer/) · [free data sources](../docs/FREE_DATA_SOURCES.md)
