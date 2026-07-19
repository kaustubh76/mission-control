#!/usr/bin/env python3
"""
Minimal LIVE swap smoke — a tiny controlled USDT -> token -> USDT round-trip through
the REAL TWAK live path (CliTwakClient.swap), to bank a genuine on-chain swap tx hash
that proves the agent's live execution works (and clears the submission's sample-swap
placeholder) WITHOUT committing contest capital into a directional position.

Guarded: refuses unless ENABLE_LIVE_TRADING=true + TWAK_MODE=live, and caps the
notional at MAX_NOTIONAL_USD so it can only ever move ~$1. Writes a proof artifact to
data/compete/. Needs the `twak` CLI on PATH (nvm v26.3.0) + funded trading wallet.

    ENABLE_LIVE_TRADING=true TWAK_MODE=live python scripts/live_swap_smoke.py --token CAKE
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ictbot.settings import settings
from ictbot.exec.twak_client import QUOTE, CliTwakClient

NOTIONAL_USD = 1.0          # the round-trip leg size
MAX_NOTIONAL_USD = 2.0      # hard guard — this script can never move more than this


def main() -> int:
    ap = argparse.ArgumentParser(description="Minimal live USDT->token->USDT round-trip (proof swap).")
    ap.add_argument("--token", default="CAKE", help="contest token to round-trip through")
    ap.add_argument("--usd", type=float, default=NOTIONAL_USD, help="leg notional in USDT")
    args = ap.parse_args()

    # --- guards ---
    if not (settings.enable_live_trading and settings.twak_mode == "live"):
        print("REFUSING: needs ENABLE_LIVE_TRADING=true AND TWAK_MODE=live.")
        return 1
    if not (settings.twak_access_id and settings.twak_hmac_secret):
        print("REFUSING: TWAK_ACCESS_ID / TWAK_HMAC_SECRET missing.")
        return 1
    if not (settings.twak_wallet_password or settings.agent_wallet_password):
        print("REFUSING: wallet password missing.")
        return 1
    if args.usd > MAX_NOTIONAL_USD:
        print(f"REFUSING: notional ${args.usd} exceeds the ${MAX_NOTIONAL_USD} safety cap.")
        return 1

    tok = args.token.upper()
    # price_fn is only used for the informational SwapResult.price field.
    client = CliTwakClient(price_fn=lambda t: 1.0)
    print(f"=== live swap smoke: {QUOTE} -> {tok} -> {QUOTE}  (${args.usd} leg) ===")

    # Quote first (no spend) so a broken route fails before we sign anything.
    q = client.swap(QUOTE, tok, args.usd, execute=False)
    print(f"  quote {QUOTE}->{tok}: out={q.amount_to} ok={q.ok} err={q.error}")
    if not q.ok or q.amount_to <= 0:
        print("  REFUSING: quote failed — not signing.")
        return 1

    # Leg 1: real buy USDT -> token.
    s1 = client.swap(QUOTE, tok, args.usd, execute=True)
    print(f"  BUY  {QUOTE}->{tok}: out={s1.amount_to} tx={s1.tx} ok={s1.ok} err={s1.error}")
    s2 = None
    if s1.ok and s1.amount_to > 0:
        time.sleep(3)  # let the buy settle before selling back
        # Leg 2: sell the received token back to USDT.
        s2 = client.swap(tok, QUOTE, s1.amount_to, execute=True)
        print(f"  SELL {tok}->{QUOTE}: out={s2.amount_to} tx={s2.tx} ok={s2.ok} err={s2.error}")

    def _leg(s):
        if s is None:
            return None
        return {"from": s.from_token, "to": s.to_token, "amount_from": s.amount_from,
                "amount_to": s.amount_to, "fee_paid": s.fee_paid, "tx": s.tx,
                "ok": s.ok, "error": s.error,
                "explorer": f"https://bscscan.com/tx/{s.tx}" if (s.ok and s.tx) else None}

    Path("data/compete").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    proof = Path(f"data/compete/live_swap_{stamp}.json")
    proof.write_text(json.dumps({
        "kind": "minimal-round-trip-proof-swap",
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trading_wallet": settings.agent_trading_address,
        "token": tok, "leg_usd": args.usd,
        "buy": _leg(s1), "sell": _leg(s2),
    }, indent=2, default=str))
    print(f"  -> wrote {proof}")

    ok = bool(s1.ok and s1.tx)
    print("  RESULT:", "SETTLED ✅" if ok else "buy did not settle ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
