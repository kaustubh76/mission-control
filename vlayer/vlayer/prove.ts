/**
 * End-to-end data-provenance proof (free, keyless):
 *   1. Notarize a real alternative.me Fear&Greed HTTPS response (zkTLS Web Proof — no API key).
 *   2. Run RegimeProver.main off-chain via the vlayer prover → get a Proof.
 *   3. Submit the Proof to RegimeVerifier on Optimism Sepolia → an on-chain attestation.
 *
 * Modeled on vlayer's `kraken-web-proof` template (server-side proof of a public price/data API — the
 * closest analog to our keyless alternative.me endpoint). RECONCILE the SDK call names/signatures below
 * against a fresh `vlayer init --template kraken-web-proof` before the first run (see ../README.md). The
 * control flow and contract wiring are correct; only `@vlayer/sdk` symbol names may shift between releases.
 */
import { createVlayerClient } from "@vlayer/sdk";
import { getConfig, createContext } from "@vlayer/sdk/config";
import { keccak256, toHex, type Address } from "viem";

import proverSpec from "../out/RegimeProver.sol/RegimeProver.json" assert { type: "json" };
import verifierSpec from "../out/RegimeVerifier.sol/RegimeVerifier.json" assert { type: "json" };
import { FNG_URL, fngHeaders } from "./config";

const PROVER_ADDRESS = process.env.PROVER_ADDRESS as Address | undefined;
const VERIFIER_ADDRESS = process.env.VERIFIER_ADDRESS as Address | undefined;

async function main() {
  const config = getConfig();
  const { chain, ethClient, account } = createContext(config);
  const agent = (process.env.AGENT_IDENTITY_ADDRESS as Address) ?? account.address;

  // OPTIONAL report binding. Default (REPORT_JSON unset) → reportHash 0x0 = the SIMPLE agent-level
  // attestation (looked up via latestOf(agent)). To bind a proof to a specific sold report, pass
  // REPORT_JSON = the EXACT canonical string `provenance.canonical_report_json(report)` produces
  // (sorted keys, compact separators) — any other formatting yields a different hash and the
  // byReport lookup will miss. See ../README.md "Optional: bind a proof to a specific report".
  const reportJson = process.env.REPORT_JSON ?? "";
  const reportHash = reportJson
    ? keccak256(toHex(reportJson))
    : ("0x" + "0".repeat(64)) as `0x${string}`;

  // 1. Fetch the notarized Web Proof of the FREE alternative.me F&G endpoint (server-side notary;
  //    no API key — the proof still attests the response genuinely came from api.alternative.me).
  //    RECONCILE: the exact notarization helper (e.g. `createWebProof` / a `vlayer` CLI call) comes
  //    from the template. Shape: give it the URL + request headers + the notary URL from config.
  const webProof = await createWebProof({
    url: FNG_URL,
    headers: fngHeaders(),
    notaryUrl: config.notaryUrl,
    token: config.token, // VLAYER_API_TOKEN
  });

  // 2. Run the Prover off-chain → Proof committing (agent, fearGreed, classification, reportHash).
  const vlayer = createVlayerClient({ url: config.proverUrl, token: config.token });
  const proveHash = await vlayer.prove({
    address: PROVER_ADDRESS!,
    proverAbi: proverSpec.abi,
    functionName: "main",
    args: [webProof, agent, reportHash],
    chainId: chain.id,
  });
  const proveResult = await vlayer.waitForProvingResult({ hash: proveHash });
  const [proof, provenAgent, fearGreed, classification, provenReportHash] = proveResult;
  console.log(`proven: agent=${provenAgent} fearGreed=${fearGreed} (${classification})`);

  // 3. Submit on-chain → RegimeVerifier.verify(...). `onlyVerified` reverts unless the proof
  //    verifies against RegimeProver.main.
  const txHash = await ethClient.writeContract({
    address: VERIFIER_ADDRESS!,
    abi: verifierSpec.abi,
    functionName: "verify",
    args: [proof, provenAgent, fearGreed, classification, provenReportHash],
    account,
    chain,
  });
  console.log(`attestation tx: ${txHash}`);
  console.log(`verify with: cast call ${VERIFIER_ADDRESS} "latestOf(address)" ${agent}`);
}

// Placeholder shim — REPLACE with the template's real notarization import/helper.
async function createWebProof(_: {
  url: string;
  headers: Record<string, string>;
  notaryUrl: string;
  token: string;
}): Promise<unknown> {
  throw new Error(
    "createWebProof is a placeholder — port the notarization helper from " +
      "`vlayer init --template kraken-web-proof` (see ../README.md §Reconcile)."
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
