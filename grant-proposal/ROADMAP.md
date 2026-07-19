# Mission Control — vlayer Grant Roadmap

**Verifiable agent commerce.**

> Mission Control is a live, self-custody agent that sells its market analysis to other agents
> (ERC-8183) on free, keyless data. This grant funds the **vlayer Web Proof** layer that makes the
> sold analysis' data provenance verifiable on-chain. Milestone-based ($1K–$10K); ask **$5K–$8K**.

**What exists:** a live mainnet agent (**381 verifiable agent transactions**), a completed **free-data
migration** (keyless), and a **complete vlayer integration scaffold** (Prover/Verifier contracts, TS
prove script, Python provenance bridge, live "Verified by vlayer" dashboard). **What's next:** the
first on-chain attestation (M1) → a demoed, buyer-verifiable proof (M4).
**Check right now:** [trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215)
· [GitHub](https://github.com/kaustubh76/BNB) · [vlayer/](../vlayer/). *Last updated: 2026-07-19.*

---

## 1. Shipped today (as of 2026-07-19)

Everything here exists now and is verifiable. Nothing here is a plan.

| Shipped | Detail | Verify |
|---|---|---|
| Live mainnet agent | **381** verifiable agent transactions (83 swaps + 221 heartbeats + 75 x402 + 2 commerce) — fully unattended | [Wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215) |
| The product vlayer secures | **2** ERC-8183 commerce jobs (25741, 26506), IPFS-pinned deliverables — the agent sells its Market Regime Report to a peer agent | ipfs://QmTXDHUPYTTFbqptJvjAsNAVPnCfaNVso9CmHpjYBb5cnp |
| **Free-data migration (complete)** | CoinMarketCap fully replaced by free, keyless sources: **Binance** (candles), **alternative.me** (F&G), **DexScreener** (DEX signals), **CoinGecko** (macro) | [../docs/FREE_DATA_SOURCES.md](../docs/FREE_DATA_SOURCES.md) |
| **vlayer contracts (scaffolded)** | `RegimeProver.sol` (verifies the alternative.me F&G Web Proof, `_parseUint` on the quoted value) + `RegimeVerifier.sol` (`onlyVerified` → `latest[agent]` / `byReport[reportHash]`) | [../vlayer/src/](../vlayer/src/) |
| **Prove script + Python bridge** | `vlayer/vlayer/prove.ts` (notarize → prove → attest on Optimism Sepolia); `provenance.py` (read-only, key-free; attaches `provenance_proof` to the sold report) | [../vlayer/](../vlayer/) |
| **Dashboard (live)** | Branded **"Verified by vlayer"** panel (electric-purple, verified-seal) + a live **Market Data Hub** showing the free stack; renders a truthful *pending* state until M1 | live dashboard |
| Test + consistency guards | Free-data adapters + provenance wiring pinned by tests (ABI ↔ contract drift guard; canonical-hash determinism); the whole Python suite green on free data | [GitHub](https://github.com/kaustubh76/BNB) |
| Grant docs | 6-criteria application + integration plan + free-data spec, all consistent with the code | [../docs/vlayer/](../docs/vlayer/) |

---

## 2. M0 — Toolchain + scaffold (DONE) [self-funded]

The scaffold is written to vlayer's documented API and modeled on the `kraken-web-proof` template:
Prover/Verifier contracts, TS prove script, Python provenance bridge, settings, and the dashboard
panel — all building/passing locally. **Exit (met):** the integration exists in-repo and is internally
consistent (verifier `verify` args == prover `main` returns; Python ABI == contract; tests green).

**Reconcile step (once, first):** install `vlayerup`, run `vlayer init --template kraken-web-proof`,
diff, and port the version strings. The contract *logic* is correct; only SDK/remapping strings shift.

---

## 3. M1 — Web Proof e2e on Optimism Sepolia [grant]

The first **on-chain attestation**: `prove.ts` notarizes a real **alternative.me** Fear & Greed HTTPS
response (keyless), runs `RegimeProver.main` off-chain via the vlayer prover, and submits the Proof
to `RegimeVerifier` → `RegimeProven` emitted on Optimism Sepolia.
**Exit:** an **attestation transaction hash** on Optimism Sepolia; the "Verified by vlayer" panel flips
from *pending* to *Verified* automatically (it already reads `latestOf(agent)`).

---

## 4. M2 — Bind the proof to the sold report [grant]

`provenance.py` reads the attestation; `build_report()` attaches a buyer-checkable `provenance_proof`
block; a served ERC-8183 job's deliverable carries an on-chain provenance pointer.
**Exit:** a served job whose sold report links a live on-chain attestation — "verify it," not "trust
me," end to end.

---

## 5. M3 — (stretch) Time Travel track record [grant]

Make the agent's headline survival claim (**13.2% worst-week drawdown** vs a 30% line) verifiable
against historical on-chain NAV heartbeats using vlayer **Time Travel**.
**Exit:** a verified drawdown attestation — a track-record claim upgraded from "trust our backtest" to
"verify our history."

---

## 6. M4 — Dashboard + demo + docs [grant]

The branded "Verified by vlayer" panel showing a live attestation, a demo video walking a reviewer
from the sold report to its on-chain proof, and the finalized grant docs.
**Exit:** live dashboard URL + demo video + submitted grant application citing the M1 attestation tx.

---

## 7. Milestone ladder

| Milestone | Status | Deliverable / gate |
|---|---|---|
| **M0** — scaffold + free data | **Done** | Integration in-repo, consistent, tested |
| **M1** — Web Proof e2e | Next (needs `vlayerup`) | Optimism Sepolia attestation tx hash |
| **M2** — bind to sold report | After M1 | Served job with a linked on-chain proof |
| **M3** — Time Travel (stretch) | Optional | Verified drawdown attestation |
| **M4** — dashboard + demo + docs | After M1–M2 | Live "Verified by vlayer" + demo video |

---

## 8. The one blocker, stated plainly

M1–M4 are gated on installing the **vlayer toolchain** (`curl -SL https://install.vlayer.xyz | bash &&
vlayerup`) — foundry + bun are already present. The prove/verify code path is written and unit-pinned;
once the toolchain is installed and the template is diffed, M1 is a short, well-understood step. Until
then, the dashboard truthfully shows *pending*. This grant is **retroactive/milestone-based**, so the
scaffold + free-data migration + live dashboard already shipped are the tangible progress; the grant
funds carrying them to a live, demoed attestation.

---

## Appendix — Canonical Numbers & Verification Kit

*Single source of truth for every figure in this proposal.*

| Metric | Value | Verify |
|---|---|---|
| Verifiable agent transactions (mainnet) | **381** (83 + 221 + 75 + 2) | [Trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215) |
| ERC-8183 commerce jobs | **2** (25741, 26506), IPFS-pinned | ipfs://QmTXDHUPYTTFbqptJvjAsNAVPnCfaNVso9CmHpjYBb5cnp |
| On-chain agent identity | ERC-8004 agentId **133085** | [Identity wallet](https://bscscan.com/address/0xEb7bF36aab4912c955474206EF0b835170389655) |
| Data stack (free, keyless) | Binance · alternative.me · DexScreener · CoinGecko | [../docs/FREE_DATA_SOURCES.md](../docs/FREE_DATA_SOURCES.md) |
| vlayer proof target | alternative.me Fear & Greed (keyless) | [../vlayer/src/RegimeProver.sol](../vlayer/src/RegimeProver.sol) |
| Attestation chain (target) | Optimism Sepolia | [../vlayer/README.md](../vlayer/README.md) |
| Grant ask | **$5,000–$8,000**, milestones M1–M4 | [../docs/vlayer/GRANT_APPLICATION.md](../docs/vlayer/GRANT_APPLICATION.md) |
| Codebase / tests | ~37.9k Python LOC · 1,624 tests | [GitHub](https://github.com/kaustubh76/BNB) |

> **Honest scope.** The vlayer integration is built and dashboard-ready but **not yet attested
> on-chain** (M1 next, gated on `vlayerup`). The free-data migration is complete and verified. The 381
> mainnet transactions, the 2 ERC-8183 jobs, and the ERC-8004 identity are real and explorer-checkable
> today. This grant funds the leap from a working scaffold to a live, buyer-verifiable attestation.

**Verification kit:** [Trading wallet](https://bscscan.com/address/0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215)
· [Agent identity 133085](https://bscscan.com/address/0xEb7bF36aab4912c955474206EF0b835170389655)
· [GitHub](https://github.com/kaustubh76/BNB) · [vlayer/](../vlayer/) · [integration plan](../docs/vlayer/INTEGRATION_PLAN.md)
