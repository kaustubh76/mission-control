"""
vlayer provenance bridge — read the on-chain data-provenance attestation.

The vlayer sub-project (`vlayer/`) proves that a FREE, keyless Fear & Greed HTTPS response was
genuinely served by `api.alternative.me` (zkTLS Web Proof — CMC is no longer available) and records
an attestation in `RegimeVerifier`. This module reads that attestation back into the Python agent
so the SOLD ERC-8183 report can carry a buyer-verifiable `provenance_proof` pointer instead of a bare
`data_provenance` string.

DESIGN (mirrors x402_cmc / identity / cmc_agent_hub):
  - Default OFF (`VLAYER_ENABLED=false`); `available()` gates every path.
  - READ-ONLY and KEY-FREE — it only calls a `view` function on the verifier, so it is safe on the
    zero-secret dashboard deploy (no wallet, no signing).
  - Best-effort + never-raise: any RPC/ABI failure returns None and the report degrades gracefully
    (the provenance field is simply omitted), exactly like the existing best-effort enrichers.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ictbot.settings import settings

log = logging.getLogger(__name__)

# Minimal ABI for the read path — matches RegimeVerifier.latestOf / reportProvenance.
_VERIFIER_ABI = [
    {
        "inputs": [{"name": "agent", "type": "address"}],
        "name": "latestOf",
        "outputs": [
            {"name": "fearGreed", "type": "uint256"},
            {"name": "classification", "type": "string"},
            {"name": "provenAt", "type": "uint64"},
            {"name": "exists", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "reportHash", "type": "bytes32"}],
        "name": "reportProvenance",
        "outputs": [
            {"name": "agent", "type": "address"},
            {"name": "fearGreed", "type": "uint256"},
            {"name": "classification", "type": "string"},
            {"name": "provenAt", "type": "uint64"},
            {"name": "exists", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


def available() -> bool:
    """True iff vlayer provenance is enabled and a verifier address is configured. Key-free — safe
    to call on every snapshot read (the read-only deploy can still READ an attestation)."""
    return bool(settings.vlayer_enabled and settings.vlayer_verifier_address)


def _agent_address() -> str | None:
    """The identity address whose attestation we read — reuse the display address (public, no key)."""
    try:
        from ictbot.agent.identity import display_address

        return display_address()
    except Exception:
        return settings.agent_identity_address or None


def _contract():
    """Build a read-only web3 handle to the verifier on the vlayer chain. Never raises here; callers
    wrap in try/except."""
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(settings.vlayer_rpc_url))
    return w3, w3.eth.contract(
        address=Web3.to_checksum_address(settings.vlayer_verifier_address),
        abi=_VERIFIER_ABI,
    )


def _iso(ts: int) -> str | None:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts)))
    except Exception:
        return None


def latest_attestation(agent: str | None = None) -> dict[str, Any] | None:
    """Read the agent's most recent proven CMC attestation, or None if unavailable / never proven.

    Returns a PUBLIC-metadata dict: chain, verifier, agent, fear_greed, classification, proven_at.
    Best-effort — any failure returns None (report omits the provenance field)."""
    if not available():
        return None
    addr = agent or _agent_address()
    if not addr:
        return None
    try:
        from web3 import Web3

        _, c = _contract()
        fg, cls, proven_at, exists = c.functions.latestOf(Web3.to_checksum_address(addr)).call()
        if not exists:
            return None
        return {
            "chain": settings.vlayer_chain,
            "verifier": settings.vlayer_verifier_address,
            "agent": addr,
            "fear_greed": int(fg),
            "classification": cls or None,
            "proven_at": _iso(proven_at),
            "attestation": "onchain",
        }
    except Exception as e:  # noqa: BLE001 — best-effort read, never sink the report
        log.debug("vlayer latest_attestation failed (%s)", type(e).__name__)
        return None


def report_provenance(report_hash: str) -> dict[str, Any] | None:
    """Read the attestation bound to a specific sold deliverable (keccak256 of the report JSON).
    Lets a buyer confirm 'this exact report's inputs were proven to come from CMC'. None on any miss."""
    if not available() or not report_hash:
        return None
    try:
        _, c = _contract()
        h = report_hash if report_hash.startswith("0x") else "0x" + report_hash
        agent, fg, cls, proven_at, exists = c.functions.reportProvenance(bytes.fromhex(h[2:])).call()
        if not exists:
            return None
        return {
            "chain": settings.vlayer_chain,
            "verifier": settings.vlayer_verifier_address,
            "agent": agent,
            "fear_greed": int(fg),
            "classification": cls or None,
            "proven_at": _iso(proven_at),
            "report_hash": h,
            "attestation": "onchain",
        }
    except Exception as e:  # noqa: BLE001
        log.debug("vlayer report_provenance failed (%s)", type(e).__name__)
        return None


def canonical_report_json(report: dict[str, Any]) -> str:
    """The ONE canonical serialization used for the OPTIONAL report-hash binding: sorted keys, compact
    separators. This exact string is what must be passed to `prove.ts` as `REPORT_JSON` (and what a
    buyer must re-serialize before hashing) for `byReport[reportHash]` to match. Deterministic."""
    import json

    return json.dumps(report, sort_keys=True, separators=(",", ":"))


def report_hash(report: dict[str, Any]) -> str | None:
    """keccak256 of `canonical_report_json(report)` — the OPTIONAL binding key. For the `byReport`
    lookup to match, `prove.ts` must hash the identical canonical string (see canonical_report_json).
    The SIMPLE path doesn't need this — it uses `latest_attestation()` (agent-level). None on failure."""
    try:
        from web3 import Web3

        # Normalize to a 0x-prefixed hex string regardless of hexbytes version (`.hex()` dropped the
        # 0x prefix in hexbytes>=1.0), so it matches viem's keccak256 output in prove.ts / EVM convention.
        hx = Web3.keccak(text=canonical_report_json(report)).hex()
        return hx if hx.startswith("0x") else "0x" + hx
    except Exception:
        return None
