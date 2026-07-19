#!/usr/bin/env python3
"""
ERC-8183 demo CLIENT — a buyer agent that pays OUR agent for a Market Regime Report.

Proves the full two-sided lifecycle end-to-end: create_job → fund → wait for the provider to
submit → settle → read the deliverable. Uses a SEPARATE keystore (a distinct agent) so the demo is
genuinely agent-to-agent. Gas is sponsored on bsc-testnet; only the escrow FUNDING transfers the
ERC-8183 payment token, so fund the client wallet from a testnet faucet first (`--show-wallet`).

Usage:
  # 1. see the client wallet address, then fund it with the testnet payment token (faucet)
  CLIENT_WALLET_PASSWORD=demo python scripts/erc8183_demo_client.py --show-wallet
  # 2. run the full lifecycle against the running provider
  CLIENT_WALLET_PASSWORD=demo python scripts/erc8183_demo_client.py --provider 0x<agent-address>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from ictbot.agent import commerce
from ictbot.settings import settings


def _client_wallet(password: str, private_key: str | None):
    from bnbagent import EVMWalletProvider

    return EVMWalletProvider(password=password, private_key=private_key or None, persist=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=os.environ.get("ERC8183_PROVIDER", ""),
                    help="provider agent address (our identity wallet)")
    ap.add_argument("--password", default=os.environ.get("CLIENT_WALLET_PASSWORD", ""),
                    help="client keystore password (a DISTINCT agent from the provider)")
    ap.add_argument("--private-key", default=os.environ.get("CLIENT_PRIVATE_KEY", ""))
    ap.add_argument("--query", default="Give me your current CMC regime read + momentum ranking.")
    ap.add_argument("--amount", type=int, default=settings.erc8183_service_price)
    ap.add_argument("--expiry-min", type=int, default=60)
    ap.add_argument("--wait-sec", type=int, default=180, help="max wait for the provider to submit")
    ap.add_argument("--show-wallet", action="store_true",
                    help="print the client wallet address (to fund it) and exit")
    args = ap.parse_args()

    if not args.password:
        print("Set CLIENT_WALLET_PASSWORD (the buyer's keystore password) or pass --password.")
        return 2
    try:
        from bnbagent import ERC8183Client, JobStatus
    except Exception as e:
        print(f"bnbagent SDK not importable: {e}")
        return 2

    wallet = _client_wallet(args.password, args.private_key)
    print(f"[client] wallet (buyer agent): {wallet.address}")
    if args.show_wallet:
        print(f"[client] fund THIS address with the testnet payment token, then re-run with --provider.")
        return 0
    if not args.provider:
        print("Pass --provider <agent address> (the seller; our identity wallet).")
        return 2

    client = ERC8183Client(wallet_provider=wallet, network=settings.erc8183_network)
    try:
        sym = client.token_symbol()
        bal = client.token_balance(wallet.address)
        print(f"[client] payment token: {sym}  balance: {bal} (need >= {args.amount} to fund)")
        if bal < args.amount:
            print(f"[client] BLOCKED: insufficient {sym}. Fund {wallet.address} from a "
                  f"bsc-testnet faucet, then re-run. (create_job is gasless; only funding moves the token.)")
            return 3
    except Exception as e:
        print(f"[client] could not read payment token (continuing best-effort): {e}")

    # 1. create the job (gasless)
    expired_at = int(time.time()) + args.expiry_min * 60
    job = client.create_job(provider=args.provider, expired_at=expired_at, description=args.query)
    job_id = int(job.get("jobId") or job.get("job_id"))
    print(f"[client] created job {job_id} (provider={args.provider})")
    commerce.journal_commerce("CREATE", job_id=job_id, provider=args.provider, query=args.query,
                              buyer=wallet.address)

    # 2. fund the escrow (transfers the payment token; gas sponsored)
    client.fund(job_id, args.amount)
    print(f"[client] funded job {job_id} with {args.amount}")
    commerce.journal_commerce("FUND", job_id=job_id, amount=args.amount)

    # 3. wait for the provider's autonomous submission
    deadline = time.time() + args.wait_sec
    status = None
    while time.time() < deadline:
        status = client.get_job_status(job_id)
        if status in (JobStatus.SUBMITTED, JobStatus.COMPLETED):
            break
        print(f"[client] waiting… status={status}")
        time.sleep(8)
    print(f"[client] status={status}")

    # 4. settle (optimistic; pulls the policy verdict) + 5. read the deliverable
    try:
        client.settle(job_id)
        commerce.journal_commerce("SETTLE", job_id=job_id, status=str(status))
        print(f"[client] settled job {job_id}")
    except Exception as e:
        print(f"[client] settle deferred (dispute window may be open): {e}")

    url = client.get_deliverable_url(job_id)
    print(f"[client] deliverable url: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
