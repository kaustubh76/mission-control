// Central config for the data-provenance vlayer scripts.
// Reconcile the `@vlayer/sdk/config` import + `getConfig()` shape with the template you diff against.

// Free, keyless Fear & Greed endpoint. alternative.me needs NO API key.
export const FNG_URL = "https://api.alternative.me/fng/?limit=1";

// alternative.me is a free public API — no auth header. We still notarize the request so the proof
// attests the response genuinely came from api.alternative.me over TLS.
export function fngHeaders(): Record<string, string> {
  return { Accept: "application/json" };
}

// keccak256 of the ERC-8183 deliverable this proof backs. Pass the report JSON string; the prove
// script hashes it so the on-chain attestation is bound to the exact sold report.
// Left 0x0 for a standalone (unbound) provenance attestation.
export const REPORT_HASH_ENV = "REPORT_HASH";

export const NETWORK = process.env.VLAYER_ENV === "testnet" ? "optimismSepolia" : "anvil";
