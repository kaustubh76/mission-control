#!/usr/bin/env python3
"""
Forward paper-trading report — the agent's track record on UNSEEN data.

A backtest only describes the past. This reads the live rebalance journal
(`data/journal/allocator_journal.jsonl`, written by run_allocator on each tick
from now → the contest) and summarises the FORWARD run: NAV path, cumulative
return, drawdown, trades, and the regime / deploy-cap / Fear&Greed evolution.
This is the real out-of-sample evidence the backtest cannot give.

Usage:  python scripts/report_forward.py
"""

from __future__ import annotations

import json
import sys

from ictbot.settings import JOURNAL_DIR

JOURNAL = JOURNAL_DIR / "allocator_journal.jsonl"


def main() -> int:
    if not JOURNAL.exists():
        print(f"no forward journal yet at {JOURNAL}. Run `make run_allocator` daily first.")
        return 1
    rows = [json.loads(line) for line in JOURNAL.read_text().splitlines() if line.strip()]
    rebals = [r for r in rows if r.get("event") == "REBALANCE"]
    halts = [r for r in rows if r.get("event") == "DD_HALT"]
    if not rebals:
        print("journal has no REBALANCE ticks yet.")
        return 1

    nav0 = rebals[0]["nav_before"]
    navs = [r["nav_after"] for r in rebals]
    peak, max_dd = nav0, 0.0
    for v in navs:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak if peak > 0 else 0.0)
    cum = navs[-1] / nav0 - 1.0 if nav0 else 0.0
    swaps = sum(r.get("n_swaps", 0) for r in rebals)
    fees = sum(r.get("fees_usd", 0.0) for r in rebals)
    caps = [r["deploy_cap"] for r in rebals if r.get("deploy_cap") is not None]
    scores = [r["regime_score"] for r in rebals if r.get("regime_score") is not None]
    fgs = [r["fear_greed"] for r in rebals if r.get("fear_greed") is not None]

    span = f"{rebals[0]['ts'][:10]} → {rebals[-1]['ts'][:10]}"
    print("=" * 78)
    print(f"FORWARD PAPER TRACK RECORD   ({len(rebals)} ticks, {span})")
    print("=" * 78)
    print(f"  NAV          {nav0:.2f} → {navs[-1]:.2f}   ({cum*100:+.2f}% cumulative)")
    print(f"  max drawdown {max_dd*100:.1f}%   (DQ gate 30%; team target ≤15%)")
    print(f"  trades       {swaps}   fees ${fees:.2f}")
    if caps:
        print(f"  deploy cap   avg {sum(caps)/len(caps):.2f}  (now {caps[-1]:.2f}, range {min(caps):.2f}-{max(caps):.2f})")
    if scores:
        print(f"  regime score avg {sum(scores)/len(scores):.2f}  (now {scores[-1]:.2f})")
    if fgs:
        print(f"  Fear&Greed   avg {sum(fgs)/len(fgs):.0f}  (now {fgs[-1]})")
    if halts:
        print(f"  ⚠ DRAWDOWN HALTS: {len(halts)} (agent flattened + stopped to protect the DQ gate)")
    # last few ticks
    print("\n  recent ticks:")
    for r in rebals[-5:]:
        held = ", ".join(f"{k}={v:.0%}" for k, v in (r.get("weights_after") or {}).items()) or "all USDT"
        print(f"    {r['ts'][:16]}  NAV {r['nav_after']:.2f}  cap {r.get('deploy_cap')}  "
              f"regime {r.get('regime_score')}  held: {held}")
    print("=" * 78)
    print("NOTE: forward paper is directional evidence + a live-readiness smoke, not proof of")
    print("edge (there is none). Its value is confirming the ADAPTIVE behaviour on unseen data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
