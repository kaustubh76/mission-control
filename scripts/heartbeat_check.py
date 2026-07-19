#!/usr/bin/env python3
"""
ERC-8004 heartbeat readiness + on-chain read-back — proves pillar 3 is wired and ACTIONABLE.

Read-only: no mint, no swap, no spend. It (1) reports the heartbeat funding-path status — the
antidote to the old silent failure (gasless MegaFuel reachable+sponsorable, OR direct-gas identity
wallet BNB ≥ floor) — and (2) reads the latest on-chain heartbeat metadata back for the agentId
(verification that a heartbeat actually landed).

  make heartbeat_check
"""

from __future__ import annotations

import json
import sys

from ictbot.agent import identity
from ictbot.settings import settings


def main() -> int:
    print(
        f"agent_id={settings.agent_id} network={settings.agent_network} "
        f"heartbeat_enabled={settings.agent_heartbeat_enabled} "
        f"use_paymaster={settings.agent_use_paymaster}"
    )
    ready = identity.heartbeat_gas_ready()
    print("gas readiness:", json.dumps(ready, default=str))
    if not ready.get("ready"):
        print("NOT READY →", ready.get("detail") or "(see readiness above)")

    # On-chain read-back (needs the local key to build the SDK agent; None on a key-free deploy).
    hb = identity.read_heartbeat()
    print("on-chain heartbeat:", json.dumps(hb, default=str) if hb else "(none yet / key-free deploy)")

    return 0 if ready.get("ready") else 1


if __name__ == "__main__":
    sys.exit(main())
