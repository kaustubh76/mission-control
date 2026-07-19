#!/usr/bin/env python3
"""
Trigger + verify the NodeReal / MegaFuel gasless link (Track-1 pillar 3 plumbing).

READ-ONLY: sends a few JSON-RPC queries (eth_chainId, eth_getTransactionCount,
pm_isSponsorable) to YOUR keyed MegaFuel endpoint — no mint, no spend. Each call
lands on your NodeReal dashboard, so this both PROVES the link is wired to your app
and reports whether the sponsor policy is live yet.

    python scripts/verify_nodereal.py                 # check testnet AND mainnet
    python scripts/verify_nodereal.py --network testnet
    python scripts/verify_nodereal.py --network mainnet

If `sponsorable` is False, the codebase link is correct but your MegaFuel SPONSOR
POLICY is not set — whitelist the printed registry + wallet at nodereal.io (and fund
the gas tank), then re-run. Once true, `register_agent.py --register` mints the
ERC-8004 identity GASLESS through this endpoint.
"""

from __future__ import annotations

import argparse
import sys

from ictbot.settings import settings


def _row(net_label: str, r: dict) -> None:
    ok = r.get("reachable")
    chain = r.get("chain_id")
    chain_ok = r.get("chain_ok")
    spons = r.get("sponsorable")
    mark = "✅" if ok and chain_ok else "❌"
    print(f"  {net_label:<8} {mark}  endpoint={r.get('endpoint', '(no key)')}")
    if not ok:
        print(f"           error: {r.get('error', 'unreachable')}")
        return
    print(f"           chain_id={chain} (expected {r.get('expected_chain_id')}, ok={chain_ok})  "
          f"wallet_nonce={r.get('nonce')}")
    spons_mark = "✅ live" if spons else ("❌ not set" if spons is False else "?")
    print(f"           sponsorable={spons_mark}  ->  {r.get('note', '')}")


def main() -> int:
    from ictbot.agent import identity

    ap = argparse.ArgumentParser()
    ap.add_argument("--network", choices=["testnet", "mainnet", "both"], default="both")
    args = ap.parse_args()

    print("=== NodeReal / MegaFuel link verification (read-only; no mint, no spend) ===")
    if not settings.nodereal_api_key:
        print("  ❌ NODEREAL_API_KEY not set in .env — nothing to verify.")
        return 1
    print(f"  identity wallet (signs mint + heartbeat + x402): {identity._identity_address()}")
    print()

    nets = (["bsc-testnet", "bsc"] if args.network == "both"
            else ["bsc-testnet"] if args.network == "testnet" else ["bsc"])
    results = {}
    for net in nets:
        r = identity.verify_paymaster_link(net)
        results[net] = r
        _row("testnet" if net == "bsc-testnet" else "mainnet", r)

    # Guidance: if any reachable link is not yet sponsorable, print the exact dashboard step
    # PER network (the registry contract differs between testnet and mainnet).
    not_sponsorable = [r for r in results.values() if r.get("reachable") and not r.get("sponsorable")]
    if not_sponsorable:
        print()
        print("  NEXT (your NodeReal dashboard — not code): create a MegaFuel sponsor policy and")
        print("  fund its gas tank, whitelisting the registry + wallet, then re-run. Once `sponsorable`")
        print("  is ✅, `register_agent.py --register` mints the ERC-8004 identity GASLESS.")
        for r in not_sponsorable:
            print(f"    - {r['network']}: registry {r.get('registry')}  +  wallet {r.get('wallet')}")

    # Exit non-zero only if a link is unreachable / wrong chain (a real wiring fault).
    bad = [r for r in results.values() if not (r.get("reachable") and r.get("chain_ok"))]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
