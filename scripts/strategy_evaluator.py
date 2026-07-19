#!/usr/bin/env python3
"""
Forward-gated strategy auto-selector — operator/cron entrypoint.

Scores every DQ-safe arm on risk-adjusted FORWARD performance, applies anti-chasing hysteresis, and:
  - drives the SIM track to the winner (proves the engine on the paper book), and
  - writes a LIVE recommendation (`data/reports/strategy_evaluation.json`) for the dashboard + operator.

LIVE is recommend-only by default — the contest arm changes only on operator sign-off (`live_tick.sh
<arm>`), or via the opt-in `STRATEGY_AUTO_APPLY_LIVE`. The whole pass is read-only w.r.t. the trade path.

Usage:
  python scripts/strategy_evaluator.py            # evaluate + persist + sim-drive + write recommendation
  python scripts/strategy_evaluator.py --dry-run  # compute + print only (no writes)
  Cron (after the forward sweep at 43 6,18):
    50 6,18 * * *  "<repo>/scripts/strategy_evaluator.sh"   # bnb-strategy-evaluator
"""

from __future__ import annotations

import argparse
import sys

from ictbot.runtime import strategy_evaluator as se


def main() -> int:
    ap = argparse.ArgumentParser(description="Forward-gated strategy auto-selector.")
    ap.add_argument("--dry-run", action="store_true", help="compute + print only; write nothing")
    args = ap.parse_args()

    d = se.run(dry_run=args.dry_run)

    print(f"[auto-select] {d['action']}  current={d['current']}  recommended={d['recommended']}"
          f"  champion={d['champion']}")
    h = d["hysteresis"]
    print(f"  hysteresis: champion held {h['consecutive']}/{h['sustain']} evals · margin {h['margin']} · "
          f"inc_score={h['incumbent_score']} champ_score={h['champion_score']}")
    print(f"  reason: {d['reason']}")
    print("  ranked (DQ-safe candidates first):")
    for s in d["scores"][:8]:
        print(f"    {s['arm']:<20} score={s['score']} dq_safe={s['dq_safe']} "
              f"fwd_eligible={s['forward_eligible']} stability={s['stability']}")
    if not args.dry_run:
        print(f"  sim_applied={d.get('sim_applied')}  live_applied={d.get('live_applied')} "
              f"(live auto-apply {'ON' if se.settings.strategy_auto_apply_live else 'OFF — recommend-only'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
