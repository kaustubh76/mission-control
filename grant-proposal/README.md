# Mission Control — vlayer Grant Proposal

**Verifiable agent commerce.** An autonomous, self-custody trading agent that sells its live market
analysis to other agents (ERC-8183) on **free, keyless data** — and uses **vlayer Web Proofs** to make
that analysis' data provenance **verifiable on-chain**. Ask: **$5,000–$8,000**, milestone-based (M1–M4).

## The three documents

| File | What it is |
|------|------------|
| [PITCH_DECK.md](PITCH_DECK.md) | 7-slide deck (Message · Speaker notes · Visual · Verify) + canonical appendix |
| [BUSINESS_PLAN.md](BUSINESS_PLAN.md) | The thesis, product, model, competition, risks, team, ask |
| [ROADMAP.md](ROADMAP.md) | Shipped-today table + milestones M0 (done) → M1–M4 (grant) |

All three share **one canonical appendix** — every figure resolves to a block explorer or a repo file.

## The one-paragraph pitch

Mission Control is a **live mainnet agent** (**381 verifiable agent transactions**) that already sells
its Market Regime Report to a peer agent (2 ERC-8183 jobs). Today the sold report's data provenance is a
*claim the buyer must trust*. **vlayer Web Proofs (zkTLS)** turn it into on-chain proof — leading with
the free, keyless **alternative.me** Fear & Greed endpoint. The integration is **built and
dashboard-ready** (Prover/Verifier contracts, TS prove script, Python provenance bridge, a live
"Verified by vlayer" panel); the grant funds the first on-chain attestation (**M1**) through a demoed,
buyer-verifiable proof (**M4**).

## Honest scope

The integration is built but **not yet attested on-chain** — M1 is gated only on installing the vlayer
toolchain (`vlayerup`). The free-data migration is complete and verified. The 381 mainnet transactions,
the 2 ERC-8183 jobs, and the ERC-8004 identity (agentId 133085) are real and explorer-checkable today.

## Related repo artifacts

[vlayer/](../vlayer/) (contracts + prove script) · [docs/vlayer/INTEGRATION_PLAN.md](../docs/vlayer/INTEGRATION_PLAN.md)
· [docs/vlayer/GRANT_APPLICATION.md](../docs/vlayer/GRANT_APPLICATION.md) · [docs/FREE_DATA_SOURCES.md](../docs/FREE_DATA_SOURCES.md)

*Styled after the Mission Control Stellar SCF proposal (`Stellar MIssion Control/pitch/`). Last updated:
2026-07-19.*
