/// <reference types="bun" />
/**
 * End-to-end data-provenance attestation (free, keyless), on Optimism Sepolia:
 *   1. Notarize a real alternative.me Fear & Greed HTTPS response  (vlayer web-proof-fetch, zkTLS — no API key)
 *   2. Deploy RegimeProver + RegimeVerifier                        (deployVlayerContracts, vlayer SDK)
 *   3. Prove RegimeProver.main off-chain via the vlayer prover     → Proof(agent, fearGreed, classification, reportHash)
 *   4. Submit to RegimeVerifier.verify on-chain                    → RegimeProven  (the M1 attestation tx)
 *
 * Ported to the vlayer 1.5.1 `kraken-web-proof` template pattern (verified against the shipped example):
 * the `web-proof-fetch` CLI for notarization, `deployVlayerContracts` for deployment, `vlayer.prove` +
 * `waitForProvingResult`, then `ethClient.writeContract`. If the SDK drifts, diff a fresh
 * `vlayer init --template kraken-web-proof` (the control flow + our contract wiring are correct).
 *
 * Prereqs in `vlayer/.env.testnet.local` (loaded by getConfig when VLAYER_ENV=testnet):
 *   VLAYER_API_TOKEN=<JWT from https://dashboard.vlayer.xyz>
 *   EXAMPLES_TEST_PRIVATE_KEY=0x<funded Optimism-Sepolia key>   (the repo .env has it as `private_key`)
 *   OPTIMISM_SEPOLIA_RPC_URL=https://sepolia.optimism.io
 * Run (from vlayer/vlayer/):  bun install && bun run prove:testnet
 */
import { createVlayerClient, type ProveArgs } from "@vlayer/sdk";
import {
  getConfig,
  createContext,
  deployVlayerContracts,
  writeEnvVariables,
} from "@vlayer/sdk/config";
import { keccak256, toHex, type Address } from "viem";
import { spawn } from "child_process";

import proverSpec from "../out/RegimeProver.sol/RegimeProver";
import verifierSpec from "../out/RegimeVerifier.sol/RegimeVerifier";
import { FNG_URL } from "./config";

const config = getConfig();
const { chain, ethClient, account, proverUrl, notaryUrl, confirmations } = createContext(config);

if (!account) {
  throw new Error("No account — set EXAMPLES_TEST_PRIVATE_KEY (0x-prefixed, funded OP-Sepolia key).");
}

const agent = (process.env.AGENT_IDENTITY_ADDRESS as Address) ?? account.address;

// OPTIONAL report binding: REPORT_JSON = the EXACT canonical string `provenance.canonical_report_json(report)`
// produces (sorted keys, compact separators). Any other formatting yields a different hash → the byReport
// lookup misses. Unset → 0x0 = the simple agent-level attestation (looked up via latestOf(agent)).
const reportJson = process.env.REPORT_JSON ?? "";
const reportHash: `0x${string}` = reportJson
  ? keccak256(toHex(reportJson))
  : (`0x${"0".repeat(64)}` as `0x${string}`);

// 1. Notarize the FREE alternative.me F&G endpoint via the vlayer notary (no API key; the proof still
//    attests the response genuinely came from api.alternative.me over TLS).
async function generateWebProof(): Promise<string> {
  const { stdout } = await runProcess("vlayer", [
    "web-proof-fetch",
    "--notary",
    String(notaryUrl),
    "--url",
    FNG_URL,
  ]);
  return stdout;
}

// 2. Deploy the pair. deployVlayerContracts deploys the prover first, then the verifier wired to it +
//    registered with the proving service (verifierArgs stays [] — the SDK injects the prover address, as
//    in the kraken template; if your RegimeVerifier ctor needs it explicitly, use verifierArgs: [prover]).
console.log("deploying RegimeProver + RegimeVerifier…");
const { prover, verifier } = await deployVlayerContracts({
  proverSpec,
  verifierSpec,
  proverArgs: [],
  verifierArgs: [],
});
await writeEnvVariables(".env", { PROVER_ADDRESS: prover, VERIFIER_ADDRESS: verifier });
console.log(`prover=${prover}  verifier=${verifier}`);

// 3. Prove RegimeProver.main off-chain → Proof(agent, fearGreed, classification, reportHash).
const webProof = await generateWebProof();
console.log("proving…");
const vlayer = createVlayerClient({ url: proverUrl, token: config.token });
const proveArgs = {
  address: prover,
  functionName: "main",
  proverAbi: proverSpec.abi,
  args: [{ webProofJson: String(webProof) }, agent, reportHash],
  chainId: chain.id,
  vgasLimit: config.vgasLimit,
} as ProveArgs<typeof proverSpec.abi, "main">;
const hash = await vlayer.prove(proveArgs);
const [proof, provenAgent, fearGreed, classification, provenReportHash] =
  await vlayer.waitForProvingResult({ hash });
console.log(`proven: agent=${provenAgent} fearGreed=${fearGreed} (${classification})`);

// 4. Submit on-chain → RegimeVerifier.verify (onlyVerified reverts unless the proof verifies).
console.log("verifying on-chain…");
const gas = await ethClient.estimateContractGas({
  address: verifier,
  abi: verifierSpec.abi,
  functionName: "verify",
  args: [proof, provenAgent, fearGreed, classification, provenReportHash],
  account,
  blockTag: "pending",
});
const txHash = await ethClient.writeContract({
  address: verifier,
  abi: verifierSpec.abi,
  functionName: "verify",
  args: [proof, provenAgent, fearGreed, classification, provenReportHash],
  chain,
  account,
  gas,
});
await ethClient.waitForTransactionReceipt({ hash: txHash, confirmations, retryCount: 60, retryDelay: 1000 });

console.log(`\n✅ M1 attestation tx: ${txHash}`);
console.log(`   verifier: ${verifier}  → set VLAYER_VERIFIER_ADDRESS on the Render API + redeploy`);
console.log(`   verify:   cast call ${verifier} "latestOf(address)" ${agent}`);

function runProcess(cmd: string, args: string[]): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args);
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("close", (code) =>
      code === 0 ? resolve({ stdout, stderr }) : reject(new Error(`web-proof-fetch failed: ${stderr}`)),
    );
    proc.on("error", reject);
  });
}
