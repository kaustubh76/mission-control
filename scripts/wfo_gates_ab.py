"""
B4 (ROADMAP §B4) — killzone + regime gates A/B harness.

Runs WFO four times per pair on the same window:
  1. no gates                  (baseline)
  2. killzone_required=True    (London/NY hours only)
  3. skip_in_low_vol=True      (regime ≠ LOW_VOL)
  4. both

Prints a per-pair table comparing TEST expectancy, WR and closures across
the four cells. The B4 acceptance bar:
  - at least one gate lifts WR by ≥ 5pp OR expectancy by ≥ 0.2R vs baseline
  - if no, leave both gates as opt-in (don't add complexity)

Usage:
  .venv/bin/python scripts/wfo_gates_ab.py --bars 50000 --grid rr2plus
  .venv/bin/python scripts/wfo_gates_ab.py --pair BTC/USDT:USDT --bars 25000

Note: requires E5 (bar-time-aware sessions) to be wired through
run_backtest, otherwise the killzone gate is a constant across the run.
That landed alongside this script.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from ictbot.engine.sweep import GRIDS
from ictbot.engine.wfo import classify, run_wfo
from ictbot.settings import PAIRS


@dataclass
class Cell:
    label: str
    killzone: bool
    low_vol: bool


CELLS = [
    Cell("baseline", killzone=False, low_vol=False),
    Cell("killzone", killzone=True, low_vol=False),
    Cell("regime",   killzone=False, low_vol=True),
    Cell("both",     killzone=True, low_vol=True),
]


def _summarise(out: dict) -> dict:
    if out.get("error") or out.get("winner") is None:
        return {"verdict": "no winner", "test_exp": None, "wr": None, "n": 0}
    w, t = out["winner"], out["test_score"]
    n = (t.get("wins") or 0) + (t.get("losses") or 0)
    verdict = classify(w["expectancy_R"], t.get("expectancy_R"), test_closures=n)
    return {
        "verdict": verdict,
        "test_exp": t.get("expectancy_R"),
        "wr": t.get("win_rate"),
        "n": n,
    }


def _run_pair(pair: str, bars: int, train_frac: float, grid: dict) -> dict:
    cells = {}
    for c in CELLS:
        print(f"\n--- {pair} | gates={c.label} ---")
        try:
            out = run_wfo(
                pair,
                bars,
                train_frac,
                grid,
                killzone_required=c.killzone,
                skip_in_low_vol=c.low_vol,
            )
            cells[c.label] = _summarise(out)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            cells[c.label] = {"verdict": "error", "test_exp": None, "wr": None, "n": 0}
    return cells


def _print_pair_table(pair: str, cells: dict) -> None:
    print(f"\n{pair}")
    print(f"{'gate':<12}{'verdict':<14}{'TEST exp':>11}{'WR':>9}{'n':>6}")
    base = cells.get("baseline", {})
    for c in CELLS:
        v = cells.get(c.label, {})
        exp = f"{v['test_exp']:+.2f}R" if v.get("test_exp") is not None else "n/a"
        wr = f"{v['wr']:.1f}%" if v.get("wr") is not None else "n/a"
        delta = ""
        if c.label != "baseline" and base.get("test_exp") is not None and v.get("test_exp") is not None:
            d_exp = v["test_exp"] - base["test_exp"]
            d_wr = (v["wr"] or 0) - (base["wr"] or 0)
            delta = f"  Δexp={d_exp:+.2f}R Δwr={d_wr:+.1f}pp"
        print(f"{c.label:<12}{v['verdict']:<14}{exp:>11}{wr:>9}{v['n']:>6}{delta}")


def _print_acceptance(pair: str, cells: dict) -> None:
    base = cells.get("baseline", {})
    if base.get("test_exp") is None or base.get("wr") is None:
        print(f"  {pair}: baseline produced no closures — can't evaluate gates.")
        return
    best_lift = None
    for c in CELLS:
        if c.label == "baseline":
            continue
        v = cells.get(c.label, {})
        if v.get("test_exp") is None or v.get("wr") is None:
            continue
        d_exp = v["test_exp"] - base["test_exp"]
        d_wr = v["wr"] - base["wr"]
        if d_exp >= 0.2 or d_wr >= 5.0:
            print(
                f"  {pair}: {c.label} CLEARS bar (Δexp={d_exp:+.2f}R, Δwr={d_wr:+.1f}pp)"
            )
            best_lift = c.label
    if best_lift is None:
        print(f"  {pair}: no gate cleared the +5pp / +0.2R bar.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pair", default=None, help="Single pair (omit with --all)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--bars", type=int, default=25000)
    ap.add_argument("--train-frac", type=float, default=0.5)
    ap.add_argument("--grid", choices=sorted(GRIDS.keys()), default="rr2plus")
    args = ap.parse_args()

    grid = GRIDS[args.grid]
    pairs = PAIRS if args.all else ([args.pair] if args.pair else PAIRS)

    table = {}
    for pair in pairs:
        table[pair] = _run_pair(pair, args.bars, args.train_frac, grid)

    print("\n" + "=" * 60)
    print(f"GATES A/B — bars={args.bars} grid={args.grid}")
    print("=" * 60)
    for pair, cells in table.items():
        _print_pair_table(pair, cells)

    print("\n" + "=" * 60)
    print("ACCEPTANCE: ≥+5pp WR OR ≥+0.2R expectancy vs baseline")
    print("=" * 60)
    for pair, cells in table.items():
        _print_acceptance(pair, cells)


if __name__ == "__main__":
    main()
