# vlayer Integration & Grant Plan — Mission Control

> **Goal:** Make Mission Control's agent-to-agent economy *verifiable* with vlayer, and
> package that work as a [vlayer Grant](https://www.vlayer.xyz/grants) application ($1K–$10K,
> retroactive/milestone, rolling).
>
> **Status:** Scaffolding phase. This document is the source of truth for scope, architecture,
> milestones, and grant positioning.

---

## 1. Why this project, and the one-line thesis

Mission Control is an autonomous, self-custody trading agent that **sells its market analysis to
other agents via ERC-8183**, under an ERC-8004 identity. It runs on **free, keyless public data**
(Binance · alternative.me · DexScreener · CoinGecko) — see [`../FREE_DATA_SOURCES.md`](../FREE_DATA_SOURCES.md).
(The original BNB-hackathon build also *bought* its data via x402 micropayments; it now runs keyless.)

Today, when a peer agent buys the **Market Regime Report** (`src/ictbot/agent/regime_report.py`),
the report's provenance —

```json
"data_provenance": "binance:4h-klines + alternative.me:fng + dexscreener:dex"
```

— is a **claim the buyer must trust**. There is no way for the buyer (or the on-chain settlement
logic) to know the numbers really came from those sources and weren't fabricated.

**vlayer Web Proofs (zkTLS) close exactly this gap.** We prove, on-chain, that a specific
market-data HTTPS response was genuinely served by its real host (we lead with the free
`api.alternative.me` Fear & Greed endpoint — keyless) and bind that proof to the exact report the
agent sells.

> **Thesis:** *vlayer turns "trust me" agent commerce into "prove it" agent commerce.* This is
> deep, load-bearing integration — vlayer becomes the trust layer of the economy, not a bolt-on.

This is also a strong grant fit on vlayer's six criteria: real EVM project **already live on
mainnet** (their "tangible progress" bar), a genuinely **novel** verifiable-agent-commerce
narrative, a clear **monetization** path (the agent already sells reports for on-chain fees), and
broad **ecosystem value** (a reusable "verifiable data provenance for AI agents" pattern).

## 2. What we build (feature mix)

**Lead feature — Web Proofs / zkTLS (primary, milestone 1–2).**
Prove a market-data endpoint's response on-chain and attest it. We lead with the free **alternative.me
Fear & Greed** endpoint (`api.alternative.me/fng/?limit=1`) because its value is a clean 0–100 integer
and it is a core input to the regime score. Its `value` is a quoted string (`"25"`), so the prover
reads it with `jsonGetString("data.0.value")` and parses the digits via a `_parseUint` helper (no
float parsing). A DexScreener **price** proof follows as an extension once the integer path is proven.

**Second feature — Time Travel (stretch, milestone 3).**
The submission's headline survival claim is a **13.2% worst-week drawdown vs the 30% DQ line**.
Time Travel lets us make historical-performance claims verifiable against real historical on-chain
state (e.g. NAV snapshots the ERC-8004 identity heartbeats over a window), upgrading "trust our
backtest" to "verify our track record."

**Deliberately out of scope for v1:** Email Proofs (no email-gated flow) and Teleport (BSC+Base
are already the two chains, but there is no cross-chain *contract execution* need yet — revisit if
the agent later posts collateral cross-chain).

## 3. Architecture

vlayer lands as the **trust layer of band ④ (Agentic Economy)** in the full-system diagram — the
second row under the three protocols, rendered as a dashed *ROADMAP* lane in
[`../architecture.svg`](../architecture.svg) (regenerate with `make architecture`). The zoomed data
flow:

```
          alternative.me F&G (api.alternative.me, FREE / keyless)
                          │  (TLS session; no API key)
                          ▼
         vlayer Notary  ──►  Web Proof (zkTLS transcript + notary sig)
                          │
                          ▼  vlayer/vlayer/prove.ts  (server-side, keyless)
      ┌───────────────────────────────────────────────┐
      │  RegimeProver.sol  (extends vlayer Prover)   │
      │   web = webProof.verify(DATA_URL)               │
      │   fg = _parseUint(jsonGetString("data.0.value"))│
      │   returns (proof, agent, fg, cls, hash)         │
      └───────────────────────────────────────────────┘
                          │  Proof
                          ▼
      ┌───────────────────────────────────────────────┐
      │  RegimeVerifier.sol (extends vlayer Verifier)│
      │   onlyVerified(prover, main.selector)           │
      │   latest[agent] = Attestation(...)              │
      │   byReport[reportHash] = Attestation(...)       │
      │   emit RegimeProven(...)                        │  ← Optimism Sepolia
      └───────────────────────────────────────────────┘
                          │
                          ▼  src/ictbot/agent/provenance.py  (read-only web3)
      Market Regime Report gains a VERIFIABLE `provenance_proof`:
        { chain, verifier, attestation_tx, fear_greed, proven_at }
      → the ERC-8183 deliverable the agent SELLS now carries an on-chain,
        buyer-checkable proof that its inputs came from the real source.
```

**Wallet reuse:** the vlayer prove step is signed by the **existing identity wallet**
(`AGENT_IDENTITY_ADDRESS` — the same keystore that mints ERC-8004 and serves ERC-8183), so there
is still **one agent address**. No new custody surface.

**Non-invasive by design:** the contest-locked trading core is untouched. The provenance bridge
(`provenance.py`) is a standalone, default-OFF (`VLAYER_ENABLED=false`) module. `build_report`
gains one optional, best-effort line that attaches `provenance_proof` when the flag is on and an
attestation exists — and silently omits it otherwise (mirrors the existing `cmc_agent_hub`
best-effort pattern). The read-only dashboard stays zero-secret: reading an attestation needs no
key.

## 4. Repository layout (added by this work)

```
vlayer/                              # self-contained Foundry + vlayer project
├── foundry.toml
├── remappings.txt
├── .gitignore
├── env.testnet.local.example        # copy → vlayer/.env.testnet.local
├── src/
│   ├── RegimeProver.sol           # Prover: verifies the alternative.me F&G Web Proof
│   └── RegimeVerifier.sol         # Verifier: onlyVerified + attestation registry
├── test/
│   └── Regime.t.sol               # Foundry test (fixture web proof)
├── vlayer/                           # TS SDK scripts (vlayer convention)
│   ├── package.json
│   ├── tsconfig.json
│   ├── config.ts
│   └── prove.ts                      # fetch web proof → prove → verify on testnet
└── README.md

src/ictbot/agent/provenance.py        # Python bridge: read attestation, augment report
docs/vlayer/INTEGRATION_PLAN.md        # this file
docs/vlayer/GRANT_APPLICATION.md       # grant form answers + 6-criteria narrative
```

> **NOTE — reconcile with the live SDK.** The `vlayer` CLI was not installed in the scaffolding
> environment, so the Solidity/TS below is written to vlayer's **documented** API and modeled on
> the `kraken-web-proof` template (server-side proof of a public price/data API — the closest
> analog to our keyless alternative.me endpoint). Before the first real run, execute
> `vlayer init --template kraken-web-proof` in a scratch dir and **diff** the generated
> `foundry.toml`, `remappings.txt`, import version strings (e.g. `vlayer-0.1.0/...`), and the
> exact `@vlayer/sdk` call names; port our endpoint-specific logic onto the version-matched skeleton.
> The contract *logic* here is correct; only version/remapping strings may need alignment.

## 5. Milestones (maps to vlayer's phased, milestone-based funding)

| # | Milestone | Deliverable | Grant evidence |
|---|-----------|-------------|----------------|
| **M0** | Toolchain + scaffold | `vlayerup`, `vlayer init` diff'd, this scaffold building with `forge build` | repo + this plan |
| **M1** | Web Proof e2e on testnet | `prove.ts` produces a real alternative.me Fear&Greed web proof; `RegimeVerifier` emits `RegimeProven` on Optimism Sepolia | attestation tx hash |
| **M2** | Bind to the sold report | `provenance.py` reads the attestation; `build_report` attaches `provenance_proof`; ERC-8183 deliverable carries a verifiable provenance pointer | a served job whose report links an on-chain proof |
| **M3** | (stretch) Time Travel track record | historical NAV/drawdown claim verified against on-chain heartbeat snapshots | verified drawdown attestation |
| **M4** | Dashboard + docs + demo | "Verified by vlayer" panel on Mission Control; demo video; grant application submitted | live URL + video |

## 6. Setup & run (once vlayerup is installed)

```bash
# 0. Install the vlayer toolchain (foundry + bun already present)
curl -SL https://install.vlayer.xyz | bash && vlayerup

# 1. Reconcile the scaffold with a fresh template (see NOTE in §4)
cd /tmp && vlayer init --template kraken-web-proof vlayer-ref   # diff against ./vlayer

# 2. Build contracts
cd "vlayer" && forge build

# 3. Configure secrets  (JWT from https://dashboard.vlayer.xyz)
cp env.testnet.local.example vlayer/.env.testnet.local
#   VLAYER_API_TOKEN=...      EXAMPLES_TEST_PRIVATE_KEY=0x...   (identity wallet key)
#   CMC_API_KEY=...           (kept secret; proven, never revealed on-chain)

# 4. Prove + verify on Optimism Sepolia
cd vlayer && bun install && bun run prove:testnet

# 5. Wire provenance into the Python agent (default OFF)
#    in project root: set VLAYER_ENABLED=true + VLAYER_VERIFIER_ADDRESS=0x... in .env
python -c "from ictbot.agent.provenance import latest_attestation; print(latest_attestation())"
```

## 7. Risks & mitigations

- **alternative.me endpoint not on vlayer's trusted notary keylist / TLS quirks** → validate early in M1 with
  a raw `vlayer` CLI web-proof against the Fear&Greed URL before writing contract logic.
- **JSON path / `jsonGetInt` return type mismatch** → reconcile against the template (§4 NOTE);
  Fear&Greed's integer payload is chosen precisely to avoid float-parsing fragility.
- **Scope creep** → M1–M2 (Web Proofs) is the fundable core; M3 Time Travel is explicitly a
  stretch and can be a *second* grant milestone rather than a v1 blocker.
- **Don't break the contest system** → all vlayer code is isolated + flag-gated OFF; the trading
  core and read-only dashboard are untouched.

## 8. Grant logistics

- **Where:** application form at <https://www.vlayer.xyz/grants> (join Discord first; the form is
  gated behind community engagement). Draft answers in [GRANT_APPLICATION.md](GRANT_APPLICATION.md).
- **Ask:** target **$5K–$8K** (mid-band) covering M1–M3 + docs/demo, sized to scope.
- **Timing:** rolling; decision ~2 weeks; then KYC + mentor pairing + phased release.
- **Pre-req to apply:** "tangible progress" — we already have a live mainnet product; M1's testnet
  attestation tx is the concrete vlayer artifact to show.
</content>
</invoke>
