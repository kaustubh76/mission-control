#!/usr/bin/env python3
"""
ERC-8183 provider — the agent runs unattended and SELLS its Market Regime Report to other agents.

Polls the ERC-8183 commerce contract for FUNDED jobs assigned to this agent, computes the live
Market Regime Report (`agent/regime_report`), and submits it on-chain (gasless on bsc-testnet via the
SDK's public MegaFuel paymaster). The same code flips to bsc-mainnet via ERC8183_NETWORK.

Usage:
  ERC8183_ENABLED=true python scripts/erc8183_serve.py            # autonomous loop (30s poll)
  ERC8183_ENABLED=true python scripts/erc8183_serve.py --once     # one poll cycle, then exit
  python scripts/erc8183_serve.py --check                         # wiring check (no loop): build
                                                                  #   job_ops + one pending-jobs read

Security: signing uses the LOCAL identity keystore (AGENT_WALLET_PASSWORD); the deliverable is
public market analysis only. See agent/commerce.py for the full security model.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from ictbot.agent import commerce
from ictbot.settings import settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run a single poll cycle then exit")
    ap.add_argument("--check", action="store_true",
                    help="wiring check only: build job_ops + read pending jobs once, no submit loop")
    ap.add_argument("--interval", type=float, default=30.0, help="poll interval seconds")
    args = ap.parse_args()

    if not commerce.available():
        print("ERC-8183 commerce unavailable: need ERC8183_ENABLED=true, the bnbagent SDK, and "
              "AGENT_WALLET_PASSWORD (the local identity keystore).")
        return 2

    print(f"[erc8183] provider on {settings.erc8183_network} "
          f"(storage={settings.erc8183_storage}, service_price={settings.erc8183_service_price})")

    if args.check:
        from ictbot.agent.identity import _identity_address

        job_ops = commerce.make_job_ops()
        pending = asyncio.run(job_ops.get_pending_jobs())  # get_pending_jobs is async
        print(f"[erc8183] wiring OK — provider={_identity_address()}")
        print(f"[erc8183] pending FUNDED jobs: {pending}")
        return 0

    stop = asyncio.Event()
    if args.once:
        # one cycle: a tiny interval + stop after first poll completes
        async def _once() -> None:
            job_ops = commerce.make_job_ops()
            from bnbagent.erc8183.server import funded_job_watcher
            asyncio.get_event_loop().call_later(5.0, stop.set)
            await funded_job_watcher(job_ops, lambda job: commerce.submit_for(job_ops, job),
                                     interval=2.0, stop=stop)
        asyncio.run(_once())
        return 0

    print(f"[erc8183] autonomous provider loop (poll {args.interval}s) — Ctrl-C to stop")
    try:
        asyncio.run(commerce.serve(interval=args.interval, stop=stop))
    except KeyboardInterrupt:
        print("\n[erc8183] stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
