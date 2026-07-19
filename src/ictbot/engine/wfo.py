"""
Walk-forward optimization: split history into TRAIN and TEST halves,
sweep configs on TRAIN, then re-evaluate the winner on TEST. If the
winning config still makes money out-of-sample, the edge is real. If it
collapses on TEST, the sweep was just curve-fitting noise.

USAGE:
  python -m ictbot.engine.wfo BTC/USDT:USDT
  python -m ictbot.engine.wfo ETH/USDT:USDT --bars 1000
  python -m ictbot.engine.wfo SOL/USDT:USDT --bars 1000 --train-frac 0.6 --quick
"""

import argparse
import sys
import time

from ictbot.engine.backtest import MIN_BARS, fetch_history, run_backtest
from ictbot.engine.sweep import GRIDS, _iter_combos, _score
from ictbot.settings import PAIRS


def classify(
    train_exp: float | None,
    test_exp: float | None,
    test_closures: int | None = None,
    min_closures: int = 10,
) -> str:
    """Classify a TRAIN/TEST expectancy pair into a WFO verdict.

    Categories:
      - "no edge"      : TRAIN ≤ 0 — the sweep never found a profitable
                         in-sample config; whatever TEST shows is noise.
      - "no closures"  : TEST produced no closed trades to evaluate.
      - "small sample" : TEST closures < min_closures — verdict
                         statistically meaningless (findings §15 PAXG
                         "✅ holds" was n=8 — fixed by F3).
      - "✅ holds"     : both halves positive AND TEST closures ≥
                         min_closures — a directionally consistent edge
                         that survived OOS with enough samples to trust.
      - "❌ overfit"   : TRAIN > 0 but TEST ≤ 0 — the sweep curve-fit.

    The TRAIN ≤ 0 short-circuit fixes the bug surfaced in
    docs/findings.md §12, where a pair could land in "✅ holds" with
    a *negative* TRAIN expectancy just because TEST happened to
    cross zero in the right direction.
    """
    if train_exp is None or train_exp <= 0:
        return "no edge"
    if test_exp is None:
        return "no closures"
    if test_exp > 0:
        if test_closures is not None and test_closures < min_closures:
            return "small sample"
        return "✅ holds"
    return "❌ overfit"


def _run_one(
    pair,
    history,
    start,
    end,
    *,
    poi_tol,
    sl,
    tp,
    fvg,
    invert=False,
    stop_mode="fraction",
    **strat_kw,
):
    """Run a single backtest restricted to [start, end] using shared history.

    `stop_mode="atr"` interprets (sl, tp) as ATR multipliers rather than
    price fractions — used by GRIDS["atr"] (ROADMAP §B2).
    """
    stop_kw = (
        {"sl_atr_mult": sl, "tp_atr_mult": tp}
        if stop_mode == "atr"
        else {"sl_frac": sl, "tp_frac": tp}
    )
    return run_backtest(
        pair,
        bars=end - start,
        verbose=False,
        quiet=True,
        history=history,
        start_idx=start,
        end_idx=end,
        poi_tolerance=poi_tol,
        require_fvg=fvg,
        invert=invert,
        **stop_kw,
        **strat_kw,
    )


def run_wfo(
    pair: str,
    bars: int,
    train_frac: float,
    grid: dict,
    invert: bool = False,
    **strat_kw,
) -> dict:
    print(f"Fetching history for {pair} ({bars} 1m bars)...")
    history = fetch_history(pair, bars)
    entry_len = len(history["entry"])

    # Split the *replayable* range, not the full fetched range.
    replay_start = max(MIN_BARS["entry"], entry_len - bars)
    total_range = entry_len - replay_start
    split = replay_start + int(total_range * train_frac)

    print(f"TRAIN: 1m bars [{replay_start} .. {split}] ({split - replay_start} bars)")
    print(f"TEST:  1m bars [{split} .. {entry_len}] ({entry_len - split} bars)")
    print()

    combos = _iter_combos(grid)
    stop_mode = grid.get("stop_mode", "fraction")
    print(f"Optimising over {len(combos)} combos on TRAIN...")
    if stop_mode == "atr":
        print("Stop mode: ATR-scaled (sl/tp values are ATR multipliers).")

    train_results = []
    for idx, (poi_tol, sl, tp, fvg) in enumerate(combos, 1):
        t0 = time.time()
        try:
            report = _run_one(
                pair,
                history,
                replay_start,
                split,
                poi_tol=poi_tol,
                sl=sl,
                tp=tp,
                fvg=fvg,
                invert=invert,
                stop_mode=stop_mode,
                **strat_kw,
            )
            s = _score(report)
            s.update(
                {"poi_tol": poi_tol, "sl": sl, "tp": tp, "require_fvg": fvg, "dt": time.time() - t0}
            )
            train_results.append(s)
            wr = f"{s['win_rate']:.1f}%" if s["win_rate"] is not None else " n/a "
            exp = f"{s['expectancy_R']:+.2f}" if s["expectancy_R"] is not None else "  n/a"
            print(
                f"  [{idx:>2}/{len(combos)}] "
                f"poi={poi_tol:<6} sl={sl:<6} tp={tp:<6} fvg={str(fvg):<5} → "
                f"sigs={s['signals']:>3} W/L={s['wins']}/{s['losses']:<3} "
                f"rate={wr:<6} exp={exp}R"
            )
        except Exception as e:
            print(f"  [{idx:>2}/{len(combos)}] FAILED: {e}")

    # Pick the winner: highest expectancy, must have >=3 closed trades on TRAIN
    eligible = [r for r in train_results if (r["wins"] + r["losses"]) >= 3]
    if not eligible:
        return {
            "pair": pair,
            "train_results": train_results,
            "winner": None,
            "test_result": None,
            "error": "No config produced >=3 closed trades on TRAIN — "
            "try --bars 1000 or --quick (smaller grid).",
        }
    eligible.sort(key=lambda r: ((r["expectancy_R"] or -999), r["signals"]), reverse=True)
    winner = eligible[0]

    print()
    print("Re-evaluating winner on TEST set (out-of-sample)...")
    test_report = _run_one(
        pair,
        history,
        split,
        entry_len,
        poi_tol=winner["poi_tol"],
        sl=winner["sl"],
        tp=winner["tp"],
        fvg=winner["require_fvg"],
        invert=invert,
        stop_mode=stop_mode,
        **strat_kw,
    )
    test_score = _score(test_report)

    return {
        "pair": pair,
        "train_results": train_results,
        "winner": winner,
        "test_score": test_score,
        "test_report": test_report,
        "error": None,
    }


def print_report(out: dict) -> None:
    print()
    print("=" * 78)
    print(f"WALK-FORWARD REPORT — {out['pair']}")
    print("=" * 78)

    if out["error"]:
        print(out["error"])
        return

    w = out["winner"]
    t = out["test_score"]
    print("WINNER (from TRAIN):")
    print(f"  poi_tol={w['poi_tol']}  sl={w['sl']}  tp={w['tp']}  require_fvg={w['require_fvg']}")
    print(
        f"  TRAIN: signals={w['signals']}  W/L={w['wins']}/{w['losses']}  "
        f"win-rate={w['win_rate']:.1f}%  expectancy={w['expectancy_R']:+.2f}R"
    )
    print(f"  TEST : signals={t['signals']}  W/L={t['wins']}/{t['losses']}  ", end="")
    if t["win_rate"] is not None:
        print(f"win-rate={t['win_rate']:.1f}%  expectancy={t['expectancy_R']:+.2f}R")
    else:
        print(f"no closed trades (still {t['open']} open)")

    print()
    test_closures = (t.get("wins") or 0) + (t.get("losses") or 0)
    verdict = classify(w["expectancy_R"], t["expectancy_R"], test_closures=test_closures)
    if verdict == "no edge":
        print(
            f"VERDICT: ⚠️  No edge in-sample — TRAIN winner is "
            f"{w['expectancy_R']:+.2f}R (≤ 0). Whatever TEST shows is noise."
        )
    elif verdict == "no closures":
        print("VERDICT: ⚠️  TEST had no closed trades. Run with more --bars.")
    elif verdict == "small sample":
        print(
            f"VERDICT: ⚠️  TEST closures = {test_closures} (< 10). "
            "Positive expectancy on this few trades is statistically meaningless."
        )
    elif verdict == "✅ holds":
        delta = t["expectancy_R"] - w["expectancy_R"]
        print(f"VERDICT: ✅ Edge holds out-of-sample (TEST exp - TRAIN exp = {delta:+.2f}R).")
    else:  # ❌ overfit
        print(
            "VERDICT: ❌ Edge collapses out-of-sample — the TRAIN winner "
            "was overfit. Don't trust the sweep result."
        )
    print()


def run_all_pairs(
    bars: int, train_frac: float, grid: dict, invert: bool = False, **strat_kw
) -> list[dict]:
    """Run WFO on every configured pair."""
    out = []
    for pair in PAIRS:
        print(f"\n=== {pair} ===")
        try:
            result = run_wfo(pair, bars, train_frac, grid, invert=invert, **strat_kw)
        except Exception as e:
            print(f"  {pair} FAILED: {e}")
            result = {"pair": pair, "error": str(e), "winner": None, "test_score": None}
        out.append(result)
    return out


def print_scoreboard(all_results: list[dict]) -> None:
    """Cross-pair verdict table: which edges actually hold out-of-sample."""
    print()
    print("=" * 88)
    print("CROSS-PAIR WALK-FORWARD SCOREBOARD")
    print("=" * 88)
    print(
        f"{'pair':<22}{'verdict':<12}{'TRAIN exp':>12}{'TEST exp':>11}"
        f"{'TEST W/L':>11}{'engine cfg':<25}"
    )
    rows = []
    for r in all_results:
        if r.get("error") or r.get("winner") is None:
            rows.append((r["pair"], "no winner", None, None, None, ""))
            continue
        w = r["winner"]
        t = r["test_score"]
        test_closures = (t.get("wins") or 0) + (t.get("losses") or 0)
        verdict = classify(w["expectancy_R"], t["expectancy_R"], test_closures=test_closures)
        cfg = f"poi={w['poi_tol']},sl={w['sl']},tp={w['tp']}"
        rows.append(
            (
                r["pair"],
                verdict,
                w["expectancy_R"],
                t.get("expectancy_R"),
                (t.get("wins"), t.get("losses")),
                cfg,
            )
        )

    # Sort: real winners first, then small-sample (might-be-edge), then
    # no-closure, then overfit, then no-edge/no-winner.
    def rank(row):
        verdict = row[1]
        if verdict == "✅ holds":
            return (0, -(row[3] or 0))
        if verdict == "small sample":
            return (1, -(row[3] or 0))
        if verdict == "no closures":
            return (2, 0)
        if verdict == "❌ overfit":
            return (3, row[3] or 0)
        if verdict == "no edge":
            return (4, -(row[2] or 0))  # least-bad TRAIN first
        return (5, 0)

    rows.sort(key=rank)
    for pair, verdict, train_exp, test_exp, wl, cfg in rows:
        te = f"{test_exp:+.2f}R" if test_exp is not None else "n/a"
        tr = f"{train_exp:+.2f}R" if train_exp is not None else "n/a"
        wl_str = f"{wl[0]}/{wl[1]}" if wl else "—"
        print(f"{pair:<22}{verdict:<12}{tr:>12}{te:>11}{wl_str:>11}  {cfg}")
    print()


def main():
    ap = argparse.ArgumentParser(description="Walk-forward optimization")
    ap.add_argument(
        "pair", nargs="?", default=None, help="Symbol, e.g. BTC/USDT:USDT (omit with --all)"
    )
    ap.add_argument("--bars", type=int, default=1000, help="Total 1m bars to use (default 1000)")
    ap.add_argument(
        "--train-frac",
        type=float,
        default=0.5,
        help="Fraction of bars used for TRAIN (default 0.5)",
    )
    ap.add_argument("--quick", action="store_true", help="Use the smaller 16-combo grid")
    ap.add_argument(
        "--grid",
        choices=sorted(GRIDS.keys()),
        default=None,
        help="Named grid (default/quick/rr2plus). Overrides --quick.",
    )
    ap.add_argument(
        "--invert", action="store_true", help="Flip every signal direction (diagnostic)"
    )
    ap.add_argument("--all", action="store_true", help="Run WFO on every pair in PAIRS")
    ap.add_argument(
        "--mss-mode",
        choices=["simple", "swing"],
        default="swing",
        help="MSS rule (default: swing, ICT-canonical; 'simple' = legacy 2-bar)",
    )
    ap.add_argument(
        "--mitigation-bars",
        type=int,
        default=None,
        help="Retire a POI N bars after it was first tapped (Phase 6 / gap S3-S5)",
    )
    ap.add_argument(
        "--tick-size",
        type=float,
        default=None,
        help="Tick size for SL/TP rounding. Default: legacy round(p, 2).",
    )
    # B4 (ROADMAP §B4): A/B gates from the CLI so we can sweep
    # {no gates, killzone, low-vol, both} on the same window.
    ap.add_argument(
        "--killzone-required",
        action="store_true",
        help="Reject entries outside London/NY killzones.",
    )
    ap.add_argument(
        "--skip-low-vol",
        action="store_true",
        help="Reject entries when ATR percentile is in the bottom 30% (Phase 7).",
    )
    ap.add_argument(
        "--delta-mode",
        choices=["sign", "relative"],
        default="sign",
        help="Delta gate: 'sign' (legacy) or 'relative' (B3, regime-normalised).",
    )
    args = ap.parse_args()

    if args.grid:
        grid = GRIDS[args.grid]
    elif args.quick:
        grid = GRIDS["quick"]
    else:
        grid = GRIDS["default"]
    strat_kw = {
        "mss_mode": args.mss_mode,
        "mitigation_bars": args.mitigation_bars,
        "tick_size": args.tick_size,
        "killzone_required": args.killzone_required,
        "skip_in_low_vol": args.skip_low_vol,
        "delta_mode": args.delta_mode,
    }

    try:
        if args.all:
            results = run_all_pairs(
                args.bars, args.train_frac, grid, invert=args.invert, **strat_kw
            )
            print_scoreboard(results)
        else:
            if not args.pair:
                ap.error("pair is required unless --all is given")
            out = run_wfo(
                args.pair, args.bars, args.train_frac, grid, invert=args.invert, **strat_kw
            )
            print_report(out)
    except Exception as e:
        print(f"WFO ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
