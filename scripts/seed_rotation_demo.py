#!/usr/bin/env python3
"""SIM-ONLY demo seeder for the dashboard 'Token Rotation' card.

The contest momentum allocator holds only `top_k` (2) tokens, so pre-contest the journal shows just
BNB + CAKE touched. The contest-floor ROTATION (`_floor_nudge`, on by default) is what reaches the
other six over the contest week with tiny ~0-NAV round-trips — but it only fires in the final days
when below the >=7-trade floor. This seeder drives that SAME rotation NOW against the PAPER broker so
the dashboard can show all 8 touched today. It is NOT a fake: it calls the real `_floor_nudge`, banks
real sim round-trips (~0 NAV minus tiny sim fees), advances + persists `floor_cursor`, and appends
genuine FLOOR_NUDGE rows (tagged `demo: true`, carrying the `tokens` touched).

Hard sim-only: refuses live mode. After running, rebuild the static fallback:
    PYTHONPATH=src python scripts/export_snapshot.py        # (or: scripts/build_web.sh)

Usage:  PYTHONPATH=src python scripts/seed_rotation_demo.py [--rounds N]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent / "run_allocator.py"


def _load_run_allocator():
    spec = importlib.util.spec_from_file_location("run_allocator_seed", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    from ictbot.settings import settings
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    ap = argparse.ArgumentParser(description="SIM-only: drive the trade-floor rotation across the universe.")
    ap.add_argument("--rounds", type=int, default=len(CONTEST_TOKENS),
                    help="distinct tokens to touch (round-trips); default = full universe")
    args = ap.parse_args()

    ra = _load_run_allocator()
    mode = "sim"  # hard sim-only — this never touches live funds

    state = ra.load_state(mode)
    if state.get("halted"):
        print("seed_rotation_demo: sim state is HALTED — `run_allocator.py --mode sim --resume` first.")
        return 2

    pf = ra.price_fn(settings.cmc_api_key or None)
    broker, client = ra.build_broker(mode, pf, state)  # active=None -> full CONTEST_TOKENS universe
    try:
        prices = broker.prices()
    except RuntimeError as e:
        print(f"seed_rotation_demo: price read failed ({e}); aborting.")
        return 2

    # 2 swaps per round-trip; N round-trips -> N distinct round-robin picks (cursor advances per trip).
    needed = 2 * max(1, args.rounds)
    swaps, banked = ra._floor_nudge(broker, prices, needed, state)
    toks = ra._nudged_tokens(swaps, broker.quote)
    if not banked:
        print(f"seed_rotation_demo: banked 0 (USDT buffer too low?). attempted {len(swaps)} leg(s).")
        return 1

    cum = int(state.get("cumulative_swaps", 0)) + banked
    state.update(balances=client.balances(), cumulative_swaps=cum)
    ra.save_state(state, mode)
    ra.journal({"ts": ra._now(), "event": "FLOOR_NUDGE", "mode": mode, "demo": True,
                "banked": banked, "cumulative_swaps": cum,
                "tokens": toks, "tx": [s.tx for s in swaps if s.ok]}, mode)
    print(f"seed_rotation_demo: banked {banked} sim swap(s) touching {toks} -> "
          f"cum={cum}, floor_cursor={state.get('floor_cursor')}. "
          f"Now rebuild snapshot.json (scripts/export_snapshot.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
