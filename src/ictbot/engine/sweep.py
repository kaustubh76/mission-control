"""
Grid-search parameters to find the combo with the best win-rate on a pair.

USAGE:
  python -m ictbot.engine.sweep BTC/USDT:USDT
  python -m ictbot.engine.sweep ETH/USDT:USDT --bars 1000
  python -m ictbot.engine.sweep SOL/USDT:USDT --quick      # smaller grid

Defaults sweep:
  POI tap tolerance : 0.0015 / 0.003 / 0.005 / 0.01
  SL fraction       : 0.003 / 0.005 / 0.008
  TP fraction       : 0.010 / 0.015 / 0.025
  Require FVG       : True / False

That's 4 x 3 x 3 x 2 = 72 combos. Each combo runs one full backtest.
"""

import argparse
import itertools
import sys
import time

from ictbot.engine.backtest import run_backtest
from ictbot.settings import PAIRS

GRIDS = {
    "default": {
        "poi_tol": [0.0015, 0.003, 0.005, 0.01],
        "sl": [0.003, 0.005, 0.008],
        "tp": [0.010, 0.015, 0.025],
        "require_fvg": [True, False],
    },
    "quick": {
        "poi_tol": [0.003, 0.01],
        "sl": [0.005, 0.008],
        "tp": [0.015, 0.025],
        "require_fvg": [True, False],
    },
    # B1 (ROADMAP §B1): RR ≥ 2:1 only. Strips loss-prone tight-RR combos
    # — findings §15 showed both holding pairs picked rr=5; failing pairs
    # picked rr=1.2–3.1 and lost to friction. Uses paired (sl, tp) tuples
    # rather than independent lists so every combo is RR-vetted.
    # 6 (sl,tp) × 4 poi_tol × 2 fvg = 48 combos.
    "rr2plus": {
        "poi_tol": [0.0015, 0.003, 0.005, 0.01],
        "sl_tp": [
            (0.003, 0.010),  # RR 3.33
            (0.003, 0.015),  # RR 5.00
            (0.003, 0.025),  # RR 8.33
            (0.005, 0.015),  # RR 3.00
            (0.005, 0.025),  # RR 5.00
            (0.008, 0.025),  # RR 3.13
        ],
        "require_fvg": [True, False],
        "stop_mode": "fraction",
    },
    # B2 (ROADMAP §B2): ATR-scaled stops. (sl, tp) here are ATR multipliers
    # — not price fractions — so friction tracks per-pair volatility instead
    # of being identical across regimes. Same 6×4×2=48 combo footprint as
    # rr2plus, but each is forwarded as sl_atr_mult/tp_atr_mult.
    "atr": {
        "poi_tol": [0.0015, 0.003, 0.005, 0.01],
        "sl_tp": [
            (0.5, 1.5),  # tight stop, modest target
            (0.5, 2.5),  # tight stop, wide target
            (1.0, 2.0),  # balanced
            (1.0, 3.0),  # balanced + room
            (1.0, 5.0),  # huge runner
            (1.5, 3.0),  # wide stop, 2x target
        ],
        "require_fvg": [True, False],
        "stop_mode": "atr",
    },
}


def _iter_combos(grid: dict) -> list:
    """Iterate (poi_tol, sl, tp, require_fvg) combos for a grid.

    Two grid shapes supported:
      - Independent: `sl` × `tp` (Cartesian product). Used by default/quick.
      - Paired: `sl_tp` is a list of (sl, tp) tuples. Used by rr2plus
        so every combo can be pre-vetted (e.g. RR ≥ 2).
    """
    if "sl_tp" in grid:
        sl_tp_pairs = list(grid["sl_tp"])
    else:
        sl_tp_pairs = list(itertools.product(grid["sl"], grid["tp"]))
    return [
        (poi, sl, tp, fvg)
        for poi in grid["poi_tol"]
        for (sl, tp) in sl_tp_pairs
        for fvg in grid["require_fvg"]
    ]


def _score(report: dict) -> dict:
    """Aggregate stats using realised per-trade net_R (see compare._score)."""
    sigs = report["signals"]
    wins = sum(1 for s in sigs if s["outcome"] == "WIN")
    losses = sum(1 for s in sigs if s["outcome"] == "LOSS")
    opens = sum(1 for s in sigs if s["outcome"] == "OPEN")
    # BE = break-even close from trailing stop; counts as closed (0R gross).
    closed = [s for s in sigs if s["outcome"] in ("WIN", "LOSS", "BE")]
    n_closed = len(closed)
    win_rate = (100.0 * wins / n_closed) if n_closed else None
    expectancy = (sum(s.get("net_R", 0) for s in closed) / n_closed) if n_closed else None
    return {
        "signals": len(sigs),
        "wins": wins,
        "losses": losses,
        "open": opens,
        "win_rate": win_rate,
        "expectancy_R": expectancy,
    }


def run_sweep(pair: str, bars: int, grid: dict) -> list:
    combos = _iter_combos(grid)
    stop_mode = grid.get("stop_mode", "fraction")
    print(f"Sweeping {len(combos)} combos on {pair} ({bars} bars each)...")
    if stop_mode == "atr":
        print("Stop mode: ATR-scaled (sl/tp values are ATR multipliers).")
    print("This will take a while — each combo is a full walk-forward backtest.")
    print()

    results = []
    for idx, (poi_tol, sl, tp, fvg) in enumerate(combos, 1):
        t0 = time.time()
        try:
            stop_kw = (
                {"sl_atr_mult": sl, "tp_atr_mult": tp}
                if stop_mode == "atr"
                else {"sl_frac": sl, "tp_frac": tp}
            )
            report = run_backtest(
                pair,
                bars=bars,
                verbose=False,
                quiet=True,
                poi_tolerance=poi_tol,
                require_fvg=fvg,
                **stop_kw,
            )
            score = _score(report)
            score.update(
                {
                    "poi_tol": poi_tol,
                    "sl": sl,
                    "tp": tp,
                    "require_fvg": fvg,
                    "dt": time.time() - t0,
                }
            )
            results.append(score)
            wr = f"{score['win_rate']:.1f}%" if score["win_rate"] is not None else " n/a "
            print(
                f"  [{idx:>2}/{len(combos)}] "
                f"poi={poi_tol:<6} sl={sl:<6} tp={tp:<6} fvg={str(fvg):<5} → "
                f"sigs={score['signals']:>3} "
                f"win={score['wins']:>3} loss={score['losses']:>3} "
                f"rate={wr:<6}  ({score['dt']:.1f}s)"
            )
        except Exception as e:
            print(f"  [{idx:>2}/{len(combos)}] FAILED: {e}")
    return results


def print_top(results: list, n: int = 10) -> None:
    # Filter to combos that actually produced closed trades
    closed = [r for r in results if (r["wins"] + r["losses"]) >= 3]
    if not closed:
        print("\nNo combo produced at least 3 closed trades — expand --bars or loosen the grid.")
        return

    # Rank by expectancy (R-multiples per trade), then by signal count
    closed.sort(key=lambda r: ((r["expectancy_R"] or -999), r["signals"]), reverse=True)

    print()
    print("=" * 88)
    print(f"TOP {n} CONFIGS (ranked by expectancy in R, requires >=3 closed trades)")
    print("=" * 88)
    print(
        f"{'poi_tol':<9}{'sl':<7}{'tp':<7}{'fvg':<6}"
        f"{'signals':>8}{'wins':>6}{'loss':>6}{'win%':>8}{'exp(R)':>9}"
    )
    for r in closed[:n]:
        wr = f"{r['win_rate']:.1f}" if r["win_rate"] is not None else "n/a"
        exp = f"{r['expectancy_R']:+.2f}" if r["expectancy_R"] is not None else "n/a"
        print(
            f"{r['poi_tol']:<9}{r['sl']:<7}{r['tp']:<7}{str(r['require_fvg']):<6}"
            f"{r['signals']:>8}{r['wins']:>6}{r['losses']:>6}{wr:>8}{exp:>9}"
        )
    print()


def run_all_pairs(bars: int, grid: dict) -> list:
    """Run the sweep on every configured pair; return scoreboard."""
    scoreboard = []
    for pair in PAIRS:
        print(f"\n--- {pair} ---")
        try:
            results = run_sweep(pair, bars, grid)
        except Exception as e:
            print(f"  {pair} FAILED: {e}")
            continue
        eligible = [r for r in results if (r["wins"] + r["losses"]) >= 3]
        if not eligible:
            scoreboard.append({"pair": pair, "best": None})
            continue
        eligible.sort(key=lambda r: ((r["expectancy_R"] or -999), r["signals"]), reverse=True)
        scoreboard.append({"pair": pair, "best": eligible[0]})
    return scoreboard


def print_scoreboard(scoreboard: list) -> None:
    print()
    print("=" * 88)
    print("SCOREBOARD (best config per pair, ranked by expectancy)")
    print("=" * 88)
    print(
        f"{'pair':<22}{'poi_tol':<9}{'sl':<7}{'tp':<7}{'fvg':<6}"
        f"{'sigs':>6}{'W':>4}{'L':>4}{'win%':>8}{'exp(R)':>9}"
    )
    rows = sorted(
        scoreboard,
        key=lambda s: s["best"]["expectancy_R"] if s["best"] else -999,
        reverse=True,
    )
    for row in rows:
        if row["best"] is None:
            print(f"{row['pair']:<22}(no combo with >=3 closed trades)")
            continue
        b = row["best"]
        wr = f"{b['win_rate']:.1f}" if b["win_rate"] is not None else "n/a"
        exp = f"{b['expectancy_R']:+.2f}"
        print(
            f"{row['pair']:<22}{b['poi_tol']:<9}{b['sl']:<7}{b['tp']:<7}"
            f"{str(b['require_fvg']):<6}{b['signals']:>6}{b['wins']:>4}"
            f"{b['losses']:>4}{wr:>8}{exp:>9}"
        )
    print()


def main():
    ap = argparse.ArgumentParser(description="Parameter sweep for ICT AI BOT")
    ap.add_argument(
        "pair", nargs="?", default=None, help="Symbol, e.g. BTC/USDT:USDT (omit with --all)"
    )
    ap.add_argument("--bars", type=int, default=500, help="Bars per backtest (default 500)")
    ap.add_argument("--quick", action="store_true", help="Use the smaller 16-combo grid")
    ap.add_argument(
        "--grid",
        choices=sorted(GRIDS.keys()),
        default=None,
        help="Named grid (default/quick/rr2plus). Overrides --quick.",
    )
    ap.add_argument(
        "--all", action="store_true", help="Sweep every pair in PAIRS and print scoreboard"
    )
    args = ap.parse_args()

    if args.grid:
        grid = GRIDS[args.grid]
    elif args.quick:
        grid = GRIDS["quick"]
    else:
        grid = GRIDS["default"]

    try:
        if args.all:
            scoreboard = run_all_pairs(args.bars, grid)
            print_scoreboard(scoreboard)
        else:
            if not args.pair:
                ap.error("pair is required unless --all is given")
            results = run_sweep(args.pair, args.bars, grid)
            print_top(results)
    except Exception as e:
        print(f"SWEEP ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
