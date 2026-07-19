"""
Run the same backtest config across every BIAS_ENGINE option and report
which one gives the best edge. Each engine is passed explicitly to the
backtest — no module-global mutation.

USAGE:
  python -m ictbot.engine.compare BTC/USDT:USDT
  python -m ictbot.engine.compare ETH/USDT:USDT --bars 5000 --invert
"""

import argparse
import sys
import time

from ictbot.engine.backtest import fetch_history, run_backtest
from ictbot.settings import PAIRS, STRATEGY_MODE

BIAS_ENGINES = ["sma", "swing", "slope"]


def _score(report: dict) -> dict:
    """Compute aggregate stats using each trade's realized net_R.

    Why not (wins*rr - losses)/closed: tight-fraction strategies on low-priced
    assets (XRP, PEPE) have per-trade RR distortion from `round(price, 2)`,
    which inflates the global expectancy. net_R is the actual realised PnL
    per trade including fees and slippage.
    """
    sigs = report["signals"]
    wins = sum(1 for s in sigs if s["outcome"] == "WIN")
    losses = sum(1 for s in sigs if s["outcome"] == "LOSS")
    opens = sum(1 for s in sigs if s["outcome"] == "OPEN")
    # BE (break-even, from trail) counts as closed for expectancy / win-rate.
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


def run_compare(
    pair: str,
    bars: int,
    *,
    poi_tolerance: float,
    sl_frac: float,
    tp_frac: float,
    require_fvg: bool,
    invert: bool,
) -> list:
    print(f"Fetching history for {pair} ({bars} 1m bars)...")
    history = fetch_history(pair, bars)
    print()

    results = []
    for engine in BIAS_ENGINES:
        t0 = time.time()
        report = run_backtest(
            pair,
            bars=bars,
            verbose=False,
            quiet=True,
            history=history,
            poi_tolerance=poi_tolerance,
            sl_frac=sl_frac,
            tp_frac=tp_frac,
            require_fvg=require_fvg,
            invert=invert,
            bias_engine=engine,
        )
        score = _score(report)
        score.update({"engine": engine, "dt": time.time() - t0})
        results.append(score)
        wr = f"{score['win_rate']:.1f}%" if score["win_rate"] is not None else " n/a "
        exp = f"{score['expectancy_R']:+.2f}" if score["expectancy_R"] is not None else "  n/a"
        print(
            f"  [{engine:<6}] sigs={score['signals']:>3} "
            f"W/L={score['wins']}/{score['losses']:<3} "
            f"open={score['open']:<3} "
            f"rate={wr:<6} exp={exp}R  ({score['dt']:.1f}s)"
        )

    return results


def print_winner(results: list) -> None:
    closed = [r for r in results if (r["wins"] + r["losses"]) >= 1]
    if not closed:
        print("\nNo engine produced any closed trades — try a longer --bars.")
        return
    closed.sort(key=lambda r: r["expectancy_R"] or -999, reverse=True)
    best = closed[0]
    print()
    print("=" * 60)
    print("WINNER")
    print("=" * 60)
    wr = f"{best['win_rate']:.1f}%" if best["win_rate"] is not None else "n/a"
    exp = f"{best['expectancy_R']:+.2f}R" if best["expectancy_R"] is not None else "n/a"
    print(f'  BIAS_ENGINE = "{best["engine"]}"')
    print(
        f"  signals={best['signals']}  W/L={best['wins']}/{best['losses']}  "
        f"win-rate={wr}  expectancy={exp}"
    )
    print(f'\n  To use this, edit ictbot/settings.py:  BIAS_ENGINE = "{best["engine"]}"')
    print()


def run_all_pairs(bars, **kw) -> dict[str, list]:
    """Run compare on every configured pair."""
    out = {}
    for pair in PAIRS:
        print(f"\n=== {pair} ===")
        try:
            out[pair] = run_compare(pair, bars, **kw)
        except Exception as e:
            print(f"  {pair} FAILED: {e}")
            out[pair] = None
    return out


def print_scoreboard(all_results: dict) -> None:
    print()
    print("=" * 80)
    print("MULTI-PAIR BIAS SCOREBOARD (best engine per pair, by expectancy)")
    print("=" * 80)
    print(f"{'pair':<22}{'engine':<8}{'sigs':>6}{'W':>4}{'L':>4}{'win%':>8}{'exp(R)':>9}")
    rows = []
    for pair, results in all_results.items():
        if not results:
            rows.append((pair, None))
            continue
        closed = [r for r in results if (r["wins"] + r["losses"]) >= 1]
        if not closed:
            rows.append((pair, None))
            continue
        closed.sort(key=lambda r: r["expectancy_R"] or -999, reverse=True)
        rows.append((pair, closed[0]))
    rows.sort(key=lambda x: x[1]["expectancy_R"] if x[1] else -999, reverse=True)
    for pair, best in rows:
        if best is None:
            print(f"{pair:<22}(no closed trades)")
            continue
        wr = f"{best['win_rate']:.1f}" if best["win_rate"] is not None else "n/a"
        exp = f"{best['expectancy_R']:+.2f}"
        print(
            f"{pair:<22}{best['engine']:<8}{best['signals']:>6}{best['wins']:>4}"
            f"{best['losses']:>4}{wr:>8}{exp:>9}"
        )
    print()


def main():
    ap = argparse.ArgumentParser(description="Compare bias engines on one pair")
    ap.add_argument(
        "pair", nargs="?", default=None, help="Symbol, e.g. BTC/USDT:USDT (omit with --all)"
    )
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--poi-tol", type=float, default=0.005)
    ap.add_argument("--sl", type=float, default=0.003)
    ap.add_argument("--tp", type=float, default=0.009)
    ap.add_argument(
        "--no-fvg",
        action="store_true",
        help="Don't require a micro FVG for entry (default: require)",
    )
    ap.add_argument(
        "--invert",
        action="store_true",
        help=f"Fade mode (override STRATEGY_MODE={STRATEGY_MODE!r})",
    )
    ap.add_argument(
        "--follow",
        action="store_true",
        help=f"Follow mode (override STRATEGY_MODE={STRATEGY_MODE!r})",
    )
    ap.add_argument("--all", action="store_true", help="Run on every pair in PAIRS")
    args = ap.parse_args()

    require_fvg = not args.no_fvg
    if args.invert:
        invert = True
    elif args.follow:
        invert = False
    else:
        invert = STRATEGY_MODE == "fade"
    kw = dict(
        poi_tolerance=args.poi_tol,
        sl_frac=args.sl,
        tp_frac=args.tp,
        require_fvg=require_fvg,
        invert=invert,
    )

    try:
        if args.all:
            all_results = run_all_pairs(args.bars, **kw)
            print_scoreboard(all_results)
        else:
            if not args.pair:
                ap.error("pair is required unless --all is given")
            results = run_compare(args.pair, args.bars, **kw)
            print_winner(results)
    except Exception as e:
        print(f"COMPARE ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
