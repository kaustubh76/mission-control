#!/usr/bin/env python3
"""
Register the agent for the BNB Hack via the official `twak` CLI.

TWAK ships a built-in `compete` command for exactly this contest — so there is NO
CompetitionRegistry ABI to reverse-engineer:

    twak compete status     # is the agent wallet registered? what's the deadline?
    twak compete register   # register the agent wallet for the BNB Hack (BSC)

Optionally mint an on-chain agent identity (the "Best Use of BNB SDK / ERC-8004"
angle):

    twak erc8004 register    # mint the ERC-8004 agent-identity NFT

Auth comes from TWAK_ACCESS_ID / TWAK_HMAC_SECRET (accepted from .env under the
TW_ or TWAK_ prefix); registration + status need the agent WALLET, so run
`twak setup` first and provide TWAK_WALLET_PASSWORD.

Usage:
  python scripts/register_agent.py                 # DRY-RUN: show commands + status
  python scripts/register_agent.py --register      # register (needs ENABLE_LIVE_TRADING)
  python scripts/register_agent.py --identity       # also mint ERC-8004 identity
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from ictbot.settings import settings

TWAK = "twak"


def _env() -> dict:
    e = dict(os.environ)
    if settings.twak_access_id:
        e["TWAK_ACCESS_ID"] = settings.twak_access_id
    if settings.twak_hmac_secret:
        e["TWAK_HMAC_SECRET"] = settings.twak_hmac_secret
    # Inject the wallet password (AGENT_/TWAK_WALLET_PASSWORD) so twak signs without
    # an interactive OS-keychain prompt (which would hang a non-interactive run).
    pw = settings.twak_wallet_password or settings.agent_wallet_password
    if pw:
        e["TWAK_WALLET_PASSWORD"] = pw
    return e


def twak(*args: str) -> tuple[int, dict | str]:
    """Run a twak subcommand; return (rc, parsed-json-or-stderr)."""
    try:
        out = subprocess.run([TWAK, *args], capture_output=True, text=True, timeout=180, env=_env())
    except FileNotFoundError:
        return 127, "twak not found on PATH — `npm i -g @trustwallet/cli` (Node >= 22.14)"
    try:
        data = json.loads(out.stdout) if out.stdout.strip() else {}
    except json.JSONDecodeError:
        data = out.stdout.strip()
    return out.returncode, (data or out.stderr.strip())


def main() -> int:
    from ictbot.agent import identity, strategy_spec

    ap = argparse.ArgumentParser()
    ap.add_argument("--register", action="store_true",
                    help="register: BNB-SDK ERC-8004 identity + twak compete (guarded)")
    ap.add_argument("--no-identity", action="store_true",
                    help="skip the BNB AI Agent SDK identity step (twak compete only)")
    args = ap.parse_args()

    have_creds = bool(settings.twak_access_id and settings.twak_hmac_secret)
    have_wallet_pw = bool(settings.agent_wallet_password)   # AGENT_/TWAK_WALLET_PASSWORD
    print("=== BNB Hack agent registration — three pillars ===")
    print(f"  CMC key               : {'✅' if settings.cmc_api_key else '❌'}  (pillar 1: data)")
    print(f"  BNB AI Agent SDK      : {'✅ installed' if identity.available() else '❌ pip install -e .[bnb]'}"
          f"  (pillar 3: identity)")
    print(f"  TWAK creds            : {'✅' if have_creds else '❌ TWAK_ACCESS_ID / TWAK_HMAC_SECRET'}  (pillar 2: exec)")
    print(f"  wallet password       : {'✅' if have_wallet_pw else '❌ set AGENT_WALLET_PASSWORD (= your twak wallet pw)'}")
    print(f"  ENABLE_LIVE_TRADING   : {settings.enable_live_trading}")

    # NodeReal/MegaFuel gasless link (pillar 3 plumbing) + x402 pay wallet readiness.
    # Read-only: proves the keyed endpoint reaches the user's app, no mint/spend.
    if settings.nodereal_api_key:
        link = identity.verify_paymaster_link()   # current AGENT_NETWORK
        reach = "✅" if (link.get("reachable") and link.get("chain_ok")) else "❌"
        spons = ("✅ live" if link.get("sponsorable")
                 else "❌ set sponsor policy" if link.get("sponsorable") is False else "?")
        print(f"  NodeReal link         : {reach} chain {link.get('chain_id')} ({link.get('network')})  "
              f"sponsorable: {spons}")
    else:
        print("  NodeReal link         : ❌ NODEREAL_API_KEY not set (gasless hits PUBLIC endpoint)")
    try:
        from ictbot.data import x402_cmc
        pay = x402_cmc.payment_address()
        if pay:
            bal = x402_cmc.base_usdc_balance(pay)
            bal_s = f"${bal:.2f}" if bal is not None else "?"
            print(f"  x402 pay wallet       : {pay}  (fund USDC on BASE; bal {bal_s}; $0.01/call)")
    except Exception:
        pass

    # Pillar 3 preview — the ERC-8004 identity the agent will mint (declares its NL
    # strategy). No private key needed: bnbagent self-manages the identity wallet.
    prof = identity.profile()
    print(f"\nBNB AI Agent SDK — ERC-8004 identity to register (network {prof['network']}):")
    print(f"  name          : {prof['name']}")
    print(f"  trading wallet: {prof['trading_wallet']}")
    print(f"  strategy      : {strategy_spec.summary()}")

    # twak compete status (needs the wallet; reports gracefully if not set up).
    print("\n$ twak compete status --json")
    _, data = twak("compete", "status", "--json")
    print(f"  -> {data}")

    if not args.register:
        print("\nDRY-RUN. To register for real: --register (needs the agent key + wallet + "
              "ENABLE_LIVE_TRADING=true). Add --no-identity to skip the BNB-SDK step.")
        return 0

    if not settings.enable_live_trading:
        print("\nREFUSING: --register needs ENABLE_LIVE_TRADING=true.")
        return 1
    if not (have_creds and have_wallet_pw):
        print("\nREFUSING: need TWAK creds + wallet password (run `twak setup`, set AGENT_WALLET_PASSWORD).")
        return 1

    rc_final = 0
    # Step 1 — BNB AI Agent SDK: mint the ERC-8004 on-chain identity (gas-free via MegaFuel).
    if not args.no_identity:
        print("\n[1/2] BNB AI Agent SDK -> register ERC-8004 identity ...")
        try:
            res = identity.register_identity()
            print(f"  -> {res}")
            # Persist the minted token id so settings.agent_id is non-zero on the next
            # boot — otherwise the recurring on-chain heartbeat (write_heartbeat) no-ops
            # forever. The SDK returns {"agentId": int, "transactionHash": ..., ...}.
            aid = (res or {}).get("agentId") if isinstance(res, dict) else None
            if aid:
                from ictbot.runtime.kill_switch import rewrite_env_key
                rewrite_env_key("AGENT_ID", str(aid))
                print(f"  -> identity minted: persisted AGENT_ID={aid} to .env "
                      f"(heartbeat now active on the next tick).")
            else:
                print("  -> WARNING: no agentId in the SDK response; AGENT_ID NOT persisted "
                      "-> the on-chain heartbeat will stay dormant. Set AGENT_ID by hand.")
        except Exception as e:
            print(f"  -> FAILED: {e}")
            rc_final = 1
    # Step 2 — TWAK: register the agent wallet for the contest.
    print("\n[2/2] $ twak compete register --json")
    rc, data = twak("compete", "register", "--json")
    print(f"  -> {data}")
    rc_final |= rc
    print("\n$ twak compete status --json  (confirm)")
    _, data = twak("compete", "status", "--json")
    print(f"  -> {data}")
    return 0 if rc_final == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
