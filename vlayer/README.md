# vlayer — Data-Provenance Attestations

Proves, on-chain, that Mission Control's **Market Regime Report** was built from data genuinely
served by its real source — using vlayer **Web Proofs (zkTLS)**. We lead with the **free, keyless
alternative.me Fear & Greed** endpoint (`api.alternative.me` — no API key). This turns the report the
agent *sells* over ERC-8183 from "trust me" into "verify it."

See [`../docs/vlayer/INTEGRATION_PLAN.md`](../docs/vlayer/INTEGRATION_PLAN.md) for the full plan and
[`../docs/vlayer/GRANT_APPLICATION.md`](../docs/vlayer/GRANT_APPLICATION.md) for the grant draft.

## M1 runbook — first on-chain attestation (verified against vlayer 1.5.1, 2026-07)

> Status: contracts (`RegimeProver`/`RegimeVerifier`) already match the current vlayer API (the v1.5.1
> `kraken-web-proof` template still imports `vlayer-0.1.0/…`, so those imports are correct). `prove.ts` is
> ported to the current pattern (`web-proof-fetch` → `deployVlayerContracts` → `prove` → `verify`). The
> only steps left are toolchain + deps + the two secrets.

1. **Install the CLI.** The old `curl -SL https://install.vlayer.xyz | bash` is **dead** (that host is
   NXDOMAIN). Get `vlayer` from GitHub Releases instead: download `binaries-<platform>.tar.gz` from
   <https://github.com/vlayer-xyz/vlayer/releases> (currently `v1.5.1`), extract, and put `bin/vlayer` on
   your `PATH` (or check <https://book.vlayer.xyz> for the current installer). Verify `vlayer --version`
   and that `forge`, `cast`, `bun` are present.
2. **Secrets** → `cp env.testnet.local.example vlayer/.env.testnet.local` and fill:
   - `VLAYER_API_TOKEN=` a JWT from <https://dashboard.vlayer.xyz> (required by the hosted prover).
   - `EXAMPLES_TEST_PRIVATE_KEY=0x…` a **funded** Optimism-Sepolia key (the repo `.env` has it as
     `private_key`; add the `0x` prefix). Fund it at an OP-Sepolia faucet.
   - `OPTIMISM_SEPOLIA_RPC_URL=https://sepolia.optimism.io`.
3. **Install deps + build.** From `vlayer/`: **`./setup.sh`** — installs all deps from the soldeer
   registry + the vlayer/risc0 GitHub release zips (`foundry.toml [dependencies]`) and runs `forge build`
   → `out/RegimeProver.sol/RegimeProver.json` + `…/RegimeVerifier.json`. **No `vlayer init` needed**
   (that path relies on vlayer's S3 example archive, which is auth-gated). Verified GREEN against
   vlayer 1.5.1. `dependencies/` is git-ignored + reproduced by the script; `soldeer.lock` pins versions.
4. **Deploy + prove + attest in one command.** `cd vlayer/vlayer && bun install && bun run prove:testnet`.
   `prove.ts` deploys the pair via the SDK, notarizes alternative.me, proves `RegimeProver.main`, and
   submits `RegimeVerifier.verify` → prints the **attestation tx** and the **verifier address**.
5. **Flip the dashboard.** Set `VLAYER_VERIFIER_ADDRESS=0x<verifier>` on the Render API service
   `srv-d9e9q6taeets73ao06v0` (`VLAYER_ENABLED=true` + `VLAYER_CHAIN` are already set) and redeploy →
   every "Verified by vlayer" surface flips from *pending* to *verified* live. Ensure the on-chain
   `agent` == `AGENT_IDENTITY_ADDRESS` so `latestOf(agent)` resolves.

## Validation status (2026-07)

- ✅ **Build green** — `./setup.sh` compiles `RegimeProver`/`RegimeVerifier` against vlayer 1.5.1 (72 files).
- ✅ **Unit tests green** — `forge test` runs `test/RegimeUnit.t.sol` (7 pass): Fear & Greed parsing bounds
  (`_parseUint`) + verifier storage/views.
- ✅ **Notarization validated** — `vlayer web-proof-fetch --url "https://api.alternative.me/fng/?limit=1"`
  produces a real MPC-TLS Web Proof of the endpoint (the notary pipeline works end-to-end).
- ✅ **Wallet funded** — the M1 deployer `0x5a64…E90c` holds **2.0 OP-Sepolia ETH**.
- ⏳ **Full prove→verify** (`test/vlayer/Regime.t.sol`) runs under `vlayer test` and needs a *dev-notary*
  web-proof fixture (the shipped examples use pre-signed dev fixtures); a `test-notary` proof is rejected by
  the local dev env. The on-chain attestation (M1) needs only the dashboard `VLAYER_API_TOKEN`.

## Contracts

| File | Role |
|------|------|
| `src/RegimeProver.sol` | Verifies the alternative.me F&G Web Proof; extracts the value; echoes `agent` + `reportHash`. |
| `src/RegimeVerifier.sol` | `onlyVerified` gate → stores attestations in `latest[agent]` and (optional) `byReport[reportHash]`. |
| `test/Regime.t.sol` | `VTest` fixture test: prove → verify → assert attestation. |
| `vlayer/prove.ts` | Notarize the alternative.me response → prove → submit attestation on Optimism Sepolia. |

*(Contracts are named `Regime*` — the proof target is the free, keyless alternative.me Fear & Greed endpoint.)*

## Simple integration (happy path)

The simple, robust path is an **agent-level attestation** (`latest[agent]`) — no report hash, no
serialization to keep in sync:

```bash
# 1. Install the vlayer toolchain (foundry + bun already present in this repo)
curl -SL https://install.vlayer.xyz | bash && vlayerup

# 2. Reconcile ONCE against a fresh template (see "Reconcile" below), then build
forge build

# 3. Secrets — JWT from https://dashboard.vlayer.xyz  (alternative.me is keyless — no data-API key)
cp env.testnet.local.example vlayer/.env.testnet.local
#   VLAYER_API_TOKEN=...    EXAMPLES_TEST_PRIVATE_KEY=0x...   (the identity/deployer wallet)

# 4. Deploy the pair, export the addresses
export PROVER_ADDRESS=0x...  VERIFIER_ADDRESS=0x...

# 5. Prove + attest on Optimism Sepolia (reportHash defaults to 0x0 = agent-level)
cd vlayer && bun install && bun run prove:testnet     # prints the attestation tx hash (grant M1 artifact)

# 6. Wire the Python agent to read it (root .env) — read-only, key-free
#   VLAYER_ENABLED=true
#   VLAYER_VERIFIER_ADDRESS=0x...   (same VERIFIER_ADDRESS)
```

Then `build_report()` attaches a `provenance_proof` block and the dashboard's **"Verified by vlayer"**
panel flips to *Verified* — both via `latestOf(agent)`. That's the whole integration.

## Prerequisites & Reconcile (do this ONCE, first)

This scaffold was hand-written to vlayer's **documented** API (the `vlayer` CLI was not available at
authoring time). Before the first build, generate a version-matched skeleton and port the logic:

```bash
cd /tmp && vlayer init --template kraken-web-proof vlayer-ref
```

Reconcile against `/tmp/vlayer-ref`:
- `foundry.toml`, `remappings.txt`, and the `lib/`/`dependencies/` layout.
- Import version strings in the `.sol` files (`vlayer-0.1.0/...` → whatever the template uses).
- `WebLib` method names (`jsonGetString`) + the `_parseUint` helper in `RegimeProver.sol` (the
  alternative.me `value` is a quoted string `"25"`, so we read a string and parse the digits).
- `@vlayer/sdk` symbols in `vlayer/prove.ts` — especially the **notarization helper** (the
  `createWebProof` placeholder shim must be replaced with the template's real server-side call, or the
  `vlayer web-proof-fetch` CLI used to produce `testdata/*.json`).

## Optional: bind a proof to a specific report (advanced)

The verifier also keys attestations by `reportHash` (`byReport[reportHash]`). This is **opt-in** and
requires an exact, shared canonical serialization on every side:

- Pass `REPORT_JSON` to `prove.ts` = the **identical string** that
  `provenance.canonical_report_json(report)` produces (`json.dumps(sort_keys=True, separators=(",",":"))`).
  Any other formatting yields a different `keccak256` → the `byReport` lookup misses.
- A buyer verifying `reportProvenance(reportHash)` must hash the report the **same** canonical way. The
  default ERC-8183 deliverable is serialized with `json.dumps(report)` (non-canonical), so a buyer
  must re-canonicalize before hashing. Prefer the simple agent-level path unless you need per-report binding.

## How the Python agent consumes it

`../src/ictbot/agent/provenance.py` reads the on-chain attestation (no key needed) and, when
`VLAYER_ENABLED=true`, `build_report()` attaches a `provenance_proof` block to the sold report:

```json
"provenance_proof": {
  "chain": "optimism-sepolia",
  "verifier": "0x...",
  "agent": "0x...",
  "fear_greed": 63,
  "proven_at": "2026-07-18T12:00:00Z",
  "attestation": "onchain"
}
```

Verify independently by calling `latestOf(agent)` (simple) or `reportProvenance(reportHash)` (bound)
on the verifier contract.
