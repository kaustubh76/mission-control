#!/usr/bin/env python3
"""
Pin the bnbagent identity key, then mint the agent's ERC-8004 identity.

Model (per the bnbagent SDK): the agent has its OWN identity wallet that bnbagent
auto-generates and encrypts to ~/.bnbagent/wallets/ (PRIVATE_KEY is "Recommended,
Auto-generate"). It is a SEPARATE wallet from the twak trading wallet
(AGENT_TRADING_ADDRESS = 0xE8A3…6215); the identity NFT it mints DECLARES the trading
wallet in its metadata, so the two are linked on-chain. That two-wallet split is
intended, not a bug.

Root cause of the lost agentId 1313 (audit H2): the auto-generated key was never
PINNED, so bnbagent regenerated a different keystore and the minted identity ended up
on a wallet whose key we no longer hold. The fix is to pin the key:

    python scripts/remint_identity.py --pin-key   # export the keystore key -> .env (AGENT_PRIVATE_KEY)
    python scripts/remint_identity.py             # dry run: print state + readiness
    python scripts/remint_identity.py --mint      # mint (only if key pinned + gas ready)

--pin-key writes AGENT_PRIVATE_KEY (+ AGENT_IDENTITY_ADDRESS) to .env via the SDK's
export_private_key(), so the identity wallet is permanent and can never be regenerated
out from under us. It prints only the address, never the key.

--mint REFUSES unless (1) the key is pinned (AGENT_PRIVATE_KEY set — else the identity
could be lost again) and (2) a gas path is actually ready (so it never broadcasts a
reverting tx). DRY-RUN by default. A mint is hard to reverse, so both guards hold.

Gas path (pick ONE):
  A. GASLESS (MegaFuel): sponsor policy on nodereal.io whitelisting the registry +
     the identity wallet, gas tank funded, until `make verify_nodereal
     ARGS="--network mainnet"` shows sponsorable ✅ (keep AGENT_USE_PAYMASTER=true).
  B. DIRECT GAS: set AGENT_USE_PAYMASTER=false and fund the identity wallet (~0.005 BNB).

On a successful mint it persists AGENT_ID to .env (so the next allocator tick
heartbeats) and writes data/compete/identity_mint_<utc-date>.json as the proof artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ictbot.settings import settings

# Direct-gas readiness: require balance >= live gasPrice x MINT_GAS_BUDGET x buffer
# (BSC runs ~0.05-3 gwei, so a real mint is ~0.00003-0.002 BNB). Falls back to a
# static floor when the gas-price read fails.
MINT_GAS_BUDGET = 900_000          # generous ceiling for an ERC-8004 register
GAS_BUFFER = 1.3
MIN_BNB_FALLBACK = 0.002           # only used when no RPC answers

# Public BSC RPCs for a keyless balance read (best-effort; the mint itself uses the
# SDK's configured endpoint, not these).
_RPCS = (
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bsc-dataseed.bnbchain.org",
)


def _bnb_balance(address: str) -> float | None:
    """Best-effort native BNB balance (ether units); None if every RPC fails."""
    try:
        from web3 import Web3
    except Exception:
        return None
    addr = Web3.to_checksum_address(address)
    for rpc in (settings.bsc_rpc_https_url or "", *_RPCS):
        if not rpc:
            continue
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
            return float(w3.from_wei(w3.eth.get_balance(addr), "ether"))
        except Exception:
            continue
    return None


def _min_bnb_direct() -> float:
    """BNB needed for a direct-gas mint at the LIVE gas price (worst-case gas budget
    x buffer); static fallback when no RPC answers."""
    try:
        from web3 import Web3
        for rpc in (settings.bsc_rpc_https_url or "", *_RPCS):
            if not rpc:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
                return float(w3.from_wei(int(w3.eth.gas_price * MINT_GAS_BUDGET * GAS_BUFFER), "ether"))
            except Exception:
                continue
    except Exception:
        pass
    return MIN_BNB_FALLBACK


def _pin_key() -> int:
    """Export the bnbagent identity keystore's private key to .env so the identity
    wallet is permanent (can't be regenerated). Prints only the address, never the key."""
    from bnbagent import EVMWalletProvider
    from ictbot.runtime.kill_switch import rewrite_env_key

    if not settings.agent_wallet_password:
        print("  ❌ AGENT_WALLET_PASSWORD (or TWAK_WALLET_PASSWORD) not set — needed to decrypt the keystore.")
        return 1
    if not Path(".env").exists():
        print("  ❌ no .env in the repo root — refusing to create one (set up .env first).")
        return 1
    try:
        w = EVMWalletProvider(password=settings.agent_wallet_password,
                              private_key=settings.agent_private_key or None, persist=True)
        pk = w.export_private_key()
        addr = w.address
    except Exception as e:
        print(f"  ❌ could not load/export the keystore: {e}")
        return 1

    if settings.agent_private_key:
        print(f"  AGENT_PRIVATE_KEY already pinned (identity wallet {addr}); nothing to do.")
        return 0

    pk = pk if pk.startswith("0x") else f"0x{pk}"
    rewrite_env_key("AGENT_PRIVATE_KEY", pk)
    rewrite_env_key("AGENT_IDENTITY_ADDRESS", addr)
    print(f"  ✅ pinned identity key to .env (AGENT_PRIVATE_KEY) + AGENT_IDENTITY_ADDRESS={addr}.")
    print("     The identity wallet is now permanent — bnbagent will reuse it, not regenerate.")
    print("     ⚠ .env now holds a plaintext key (gitignored). Back up ~/.bnbagent/wallets/ too.")
    return 0


def _patch_sdk_gas_floor() -> float:
    """bnbagent hardcodes MIN_GAS_PRICE_WEI = 3 gwei — ~60x BSC's current real price —
    which prices a direct-gas mint at ~0.0037 BNB and overshoots small balances.
    Lower the floor to max(2x live gas price, 0.1 gwei) for THIS process only.
    Must patch BOTH the defining module and erc8004.contract's by-value import."""
    from web3 import Web3
    live = None
    for rpc in (settings.bsc_rpc_https_url or "", *_RPCS):
        if not rpc:
            continue
        try:
            live = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15})).eth.gas_price
            break
        except Exception:
            continue
    floor = max(int((live or 0) * 2), 100_000_000)  # never below 0.1 gwei (BSC inclusion floor)
    import bnbagent.core.contract_mixin as cm
    import bnbagent.erc8004.contract as ec
    cm.MIN_GAS_PRICE_WEI = floor
    ec.MIN_GAS_PRICE_WEI = floor
    return floor / 1e9


def main() -> int:
    from ictbot.agent import identity

    ap = argparse.ArgumentParser(description="Pin the identity key and mint the agent's ERC-8004 identity.")
    ap.add_argument("--pin-key", action="store_true",
                    help="export the bnbagent keystore's key to .env (AGENT_PRIVATE_KEY) and exit")
    ap.add_argument("--mint", action="store_true",
                    help="actually mint (refused unless key pinned AND gas ready); default is dry-run")
    args = ap.parse_args()

    if not identity.available():
        print("  ❌ bnbagent not installed — `python -m pip install -e \".[bnb]\"`.")
        return 1

    if args.pin_key:
        print("=== Pin bnbagent identity key -> .env ===")
        return _pin_key()

    print("=== ERC-8004 identity — status + mint ===")
    if not settings.agent_wallet_password:
        print("  ❌ AGENT_WALLET_PASSWORD (or TWAK_WALLET_PASSWORD) not set — it encrypts the keystore.")
        return 1

    wallet = identity._identity_address()  # AGENT_PRIVATE_KEY if pinned, else the keystore
    if not wallet:
        print("  ❌ could not derive the identity wallet from the keystore/password.")
        return 1

    network = settings.agent_network
    use_paymaster = settings.agent_use_paymaster
    trading_wallet = settings.agent_trading_address
    key_pinned = bool(settings.agent_private_key)
    bal = _bnb_balance(wallet)
    link = identity.verify_paymaster_link() if settings.nodereal_api_key else {}
    sponsorable = bool(link.get("sponsorable"))

    print(f"  identity wallet : {wallet}  (bnbagent signs the mint from THIS key)")
    print(f"  trading wallet  : {trading_wallet or '(AGENT_TRADING_ADDRESS unset)'}  (declared in the identity metadata)")
    print(f"  key pinned      : {'✅ AGENT_PRIVATE_KEY set' if key_pinned else '❌ ephemeral (run --pin-key first)'}")
    print(f"  network         : {network}")
    print(f"  current AGENT_ID: {settings.agent_id or '(unset)'}")
    print(f"  BNB balance     : {f'{bal:.6f} BNB' if bal is not None else '? (RPC read failed)'}")
    print(f"  AGENT_USE_PAYMASTER: {use_paymaster}")
    print(f"  MegaFuel sponsorable: {'✅ live' if sponsorable else '❌ not set'}")

    # Two-wallet pattern is intended (the identity declares the trading wallet); just
    # surface it so the relationship is explicit.
    if trading_wallet and wallet.lower() != trading_wallet.lower():
        print(f"  note: identity wallet ≠ trading wallet — expected; the NFT metadata links "
              f"trading_wallet={trading_wallet}.")

    # GUARD 1 — the key MUST be pinned before minting. An ephemeral auto-generated key
    # is exactly how agentId 1313 was lost (regenerated, then unrecoverable).
    if not key_pinned:
        print("\n  ❌ KEY NOT PINNED — refusing to mint from an ephemeral keystore.")
        print("     Run:  make remint_identity ARGS=\"--pin-key\"   (writes AGENT_PRIVATE_KEY to .env)")
        print("     then re-run this. Pinning makes the identity wallet permanent.")
        return 0

    # GUARD 2 — a gas path must be ready, or the mint tx reverts.
    min_direct = _min_bnb_direct()
    gasless_ready = use_paymaster and sponsorable
    direct_ready = (not use_paymaster) and (bal is not None) and (bal >= min_direct)
    ready = gasless_ready or direct_ready
    path = "gasless (MegaFuel)" if gasless_ready else "direct-gas" if direct_ready else "none"
    print(f"  direct-gas need : {min_direct:.6f} BNB (live gas price x {MINT_GAS_BUDGET//1000}k x {GAS_BUFFER})")
    print(f"  gas path ready  : {'✅ ' + path if ready else '❌ none'}")

    if not ready:
        print("\n  NOT READY — pick ONE gas path (your action, not code):")
        print(f"    A. GASLESS: MegaFuel sponsor policy on nodereal.io whitelisting")
        print(f"       registry {link.get('registry', '(run verify_nodereal)')}")
        print(f"       wallet   {wallet}")
        print(f"       fund its gas tank; re-check `make verify_nodereal ARGS=\"--network mainnet\"`")
        print(f"       until sponsorable ✅ (keep AGENT_USE_PAYMASTER=true).")
        print(f"    B. DIRECT GAS: set AGENT_USE_PAYMASTER=false and send ~0.005 BNB to {wallet}.")
        print("  Then: make remint_identity ARGS=\"--mint\"")
        return 0  # blocked on a prerequisite, not an error

    if not args.mint:
        print(f"\n  READY via {path}. DRY-RUN — re-run with --mint to fire the mainnet mint.")
        return 0

    # --- live mint (both guards passed) ---
    if direct_ready:
        gwei = _patch_sdk_gas_floor()
        print(f"  (patched the SDK's 3-gwei floor -> {gwei:.2f} gwei for this process)")
    print(f"\n[mint] minting the ERC-8004 identity via {path} ...")
    try:
        res = identity.register_identity()
    except Exception as e:
        print(f"  -> FAILED: {e}")
        return 1
    print(f"  -> {res}")

    aid = (res or {}).get("agentId") if isinstance(res, dict) else None
    tx = (res or {}).get("transactionHash") if isinstance(res, dict) else None

    if aid:
        from ictbot.runtime.kill_switch import rewrite_env_key
        rewrite_env_key("AGENT_ID", str(aid))
        print(f"  -> persisted AGENT_ID={aid} to .env (heartbeat active next tick; "
              f"ensure AGENT_HEARTBEAT_ENABLED=true).")
    else:
        print("  -> WARNING: no agentId in the SDK response; set AGENT_ID by hand from the mint tx logs.")

    # Submission proof artifact (mirrors the registration proof pack).
    Path("data/compete").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    proof = Path(f"data/compete/identity_mint_{stamp}.json")
    proof.write_text(json.dumps({
        "minted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "network": network,
        "identity_wallet": wallet,
        "trading_wallet": trading_wallet,
        "agent_id": aid,
        "transaction_hash": tx,
        "gas_path": path,
        "result": res if isinstance(res, dict) else str(res),
    }, indent=2, default=str))
    print(f"  -> wrote {proof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
