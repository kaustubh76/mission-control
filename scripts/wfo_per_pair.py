"""
Fix 9.A driver: run WFO independently for each configured pair and
write a JSON report with the per-pair winners.

The plumbing in `src/ictbot/engine/wfo.py` already accepts a single pair
+ grid. This wrapper just sweeps every pair in `settings.PAIRS`,
captures the winning `(sl_frac, tp_frac)` per pair plus the classify
verdict, and saves a single JSON for ops to consume.

USAGE:
  python3 scripts/wfo_per_pair.py
  python3 scripts/wfo_per_pair.py --bars 10000 --grid rr2plus
  python3 scripts/wfo_per_pair.py --quick           # fast iteration

Output:
  data/wfo/per_pair_<UTC-date>.json — single dict with one entry per pair.

Acceptance reading:
  - A pair with `verdict in {"✅ holds", "small sample"}` and `train_exp
    > 0` is a candidate for promoting its (sl, tp) into the per-pair
    env defaults (Fix 9.A).
  - A pair with `verdict in {"no edge", "❌ overfit"}` should NOT have
    its values promoted — the global default stays.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ictbot.engine.sweep import GRIDS
from ictbot.engine.wfo import classify, run_wfo
from ictbot.settings import PAIRS, PROJECT_ROOT


def _wfo_one(pair: str, *, bars: int, grid_name: str, train_frac: float) -> dict:
    grid = GRIDS[grid_name]
    try:
        out = run_wfo(pair, bars=bars, train_frac=train_frac, grid=grid)
    except Exception as exc:
        return {"pair": pair, "error": str(exc), "winner": None}
    winner = out.get("winner")
    test_score = out.get("test_score") or {}
    test_closures = (test_score.get("wins") or 0) + (test_score.get("losses") or 0)
    verdict = (
        classify(
            winner["expectancy_R"] if winner else None,
            test_score.get("expectancy_R"),
            test_closures=test_closures,
        )
        if winner
        else "no winner"
    )
    return {
        "pair": pair,
        "verdict": verdict,
        "grid": grid_name,
        "bars": bars,
        "train_frac": train_frac,
        "winner": (
            {
                "sl": winner["sl"],
                "tp": winner["tp"],
                "poi_tol": winner["poi_tol"],
                "require_fvg": winner["require_fvg"],
                "train_expectancy_R": winner["expectancy_R"],
                "train_wins": winner["wins"],
                "train_losses": winner["losses"],
            }
            if winner
            else None
        ),
        "test": (
            {
                "expectancy_R": test_score.get("expectancy_R"),
                "wins": test_score.get("wins"),
                "losses": test_score.get("losses"),
                "win_rate": test_score.get("win_rate"),
            }
            if test_score
            else None
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument(
        "--bars",
        type=int,
        default=10000,
        help="Total 1m bars per pair (default 10000)",
    )
    ap.add_argument(
        "--train-frac",
        type=float,
        default=0.5,
        help="TRAIN fraction (default 0.5)",
    )
    ap.add_argument(
        "--grid",
        choices=sorted(GRIDS.keys()),
        default="rr2plus",
        help="Grid name (default rr2plus, the Phase E winner family)",
    )
    ap.add_argument(
        "--quick",
        action="store_true",
        help="Use the 16-combo quick grid (overrides --grid)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default data/wfo/per_pair_<UTC-date>.json)",
    )
    args = ap.parse_args()

    grid_name = "quick" if args.quick else args.grid

    out_dir = PROJECT_ROOT / "data" / "wfo"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.out is None:
        today = datetime.now(timezone.utc).date().isoformat()
        args.out = out_dir / f"per_pair_{today}.json"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grid": grid_name,
        "bars": args.bars,
        "train_frac": args.train_frac,
        "pairs": {},
    }

    for pair in PAIRS:
        print(f"\n=== {pair} ===")
        result = _wfo_one(
            pair, bars=args.bars, grid_name=grid_name, train_frac=args.train_frac
        )
        report["pairs"][pair] = result
        verdict = result.get("verdict", "?")
        w = result.get("winner")
        if w:
            print(
                f"  → verdict={verdict} sl={w['sl']} tp={w['tp']} "
                f"train_exp={w['train_expectancy_R']:+.2f}R"
            )
        else:
            print(f"  → {verdict} ({result.get('error', 'no winner')})")

    args.out.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {args.out}")

    # Summary banner.
    print("\n" + "=" * 72)
    print(f"{'pair':<22}{'verdict':<14}{'sl':>8}{'tp':>8}{'train_exp':>14}")
    print("-" * 72)
    for pair, r in report["pairs"].items():
        v = r.get("verdict", "?")
        w = r.get("winner") or {}
        sl = w.get("sl", "—")
        tp = w.get("tp", "—")
        exp = w.get("train_expectancy_R")
        exp_s = f"{exp:+.2f}R" if exp is not None else "n/a"
        print(f"{pair:<22}{v:<14}{str(sl):>8}{str(tp):>8}{exp_s:>14}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
