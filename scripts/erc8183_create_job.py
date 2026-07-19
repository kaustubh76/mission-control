#!/usr/bin/env python3
"""
ERC-8183 "create a job" — operator pipeline (buyer side).

Runs the FULL real loop end-to-end so OUR agent serves a genuine paid job and the dashboard ledger
fills (jobs_served, revenue, tx): buyer create → fund → provider submits on-chain → buyer settle.
Self-contained (no separate serve loop needed) — it reuses `ictbot.agent.commerce.create_and_serve_job`.

NETWORK: set by `ERC8183_NETWORK` (default bsc-testnet; use `bsc-mainnet` for real on-chain commerce).
  - testnet: gasless via the SDK's public MegaFuel.
  - mainnet: gasless when `AGENT_USE_PAYMASTER=true` (+ `NODEREAL_API_KEY`), else DIRECT-GAS — the
    buyer + provider wallets each need a little BNB.

KEYS (local operator only — never the read-only deploy):
  ERC8183_ENABLED=true
  AGENT_WALLET_PASSWORD / AGENT_PRIVATE_KEY  — the PROVIDER (our identity) keystore
  CLIENT_WALLET_PASSWORD [+ CLIENT_WALLET_DIR | CLIENT_WALLET_ADDRESS | CLIENT_PRIVATE_KEY] — the BUYER

Usage:
  # 1. See the buyer address + the EXACT payment token to fund (read-only, no value moves):
  ERC8183_ENABLED=true ERC8183_NETWORK=bsc-mainnet CLIENT_WALLET_PASSWORD=... \
    CLIENT_WALLET_DIR=~/.bnbagent/buyer python scripts/erc8183_create_job.py --show-wallet
  # 2. Fund THAT buyer address with the payment token (and a little BNB if not gasless), then run:
  ERC8183_ENABLED=true ERC8183_NETWORK=bsc-mainnet CLIENT_WALLET_PASSWORD=... \
    CLIENT_WALLET_DIR=~/.bnbagent/buyer python scripts/erc8183_create_job.py
"""

from __future__ import annotations

import argparse
import json
import sys

from ictbot.agent import commerce
from ictbot.settings import settings


def main() -> int:
    ap = argparse.ArgumentParser(description="Create + serve a real ERC-8183 job (operator).")
    ap.add_argument("--show-wallet", action="store_true",
                    help="print the buyer address + payment token to fund, then exit (no job)")
    ap.add_argument("--settle-pending", action="store_true",
                    help="FINALIZE served-but-unsettled jobs whose dispute window has closed (no new job)")
    ap.add_argument("--job-ids", type=int, nargs="*", default=None,
                    help="with --settle-pending: specific job ids to settle (default: all unsettled)")
    ap.add_argument("--query", default="Give me your current CMC regime read + momentum ranking.")
    ap.add_argument("--amount", type=int, default=None,
                    help=f"payment-token units to fund (default: ERC8183_SERVICE_PRICE={settings.erc8183_service_price})")
    ap.add_argument("--expiry-min", type=int, default=20160)  # 14d — MUST exceed the ~7d dispute
    # window, else submission deadline (expiredAt - disputeWindow) is already past. Loop runs in seconds.
    args = ap.parse_args()

    if not commerce.buyer_available():
        print("buyer-side commerce unavailable. Need (LOCAL operator run):\n"
              "  ERC8183_ENABLED=true, the bnbagent SDK, AGENT_WALLET_PASSWORD (provider), and\n"
              "  CLIENT_WALLET_PASSWORD (a distinct buyer keystore). See this script's docstring.")
        return 2

    if args.show_wallet:
        info = commerce.buyer_wallet_info()
        print(json.dumps(info, indent=2))
        need = args.amount if args.amount is not None else info.get("price")
        bal = info.get("balance")
        if info.get("token_address"):
            print(f"\nFUND the buyer {info['buyer']} on {info['network']} with >= {need} "
                  f"{info.get('token') or 'U'} (token {info['token_address']})"
                  + (f"; current balance {bal}." if bal is not None else "."))
            print("If not gasless (AGENT_USE_PAYMASTER!=true), also fund the buyer + provider with a little BNB.")
        return 0

    if args.settle_pending:
        ids = args.job_ids if args.job_ids else None
        pending = commerce.unsettled_job_ids() if ids is None else ids
        print(f"[settle] network={settings.erc8183_network} — finalizing {len(pending)} job(s): {pending}")
        result = commerce.settle_pending_jobs(ids)
        print(json.dumps(result, indent=2))
        # Deferred (dispute window still open) is an EXPECTED outcome, not a failure → exit 0.
        return 0 if result.get("ok") else 1

    print(f"[create-job] network={settings.erc8183_network} — running the real loop "
          "(create → fund → serve → settle)…")
    result = commerce.create_and_serve_job(args.query, amount=args.amount, expiry_min=args.expiry_min)
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        # A fund-precheck (insufficient balance) is an expected, actionable outcome — not a crash.
        return 0 if result.get("stage") == "fund-precheck" else 1
    print(f"\n✓ served job #{result.get('job_id')} on {result.get('network')} — "
          f"tx {result.get('tx')}  (dashboard ledger now shows jobs_served≥1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
