"""
Run a backtest and persist its cumulative net_R equity curve to
data/runs/backtest_curve.json so the Streamlit dashboard can render it
without re-running the backtest each refresh.

USAGE:
  python -m ictbot.engine.bt_curve BTC/USDT:USDT
  python -m ictbot.engine.bt_curve ETH/USDT:USDT --bars 5000 --sl 0.005 --tp 0.015
"""

import argparse
import json
import sys

from ictbot.engine.backtest import run_backtest
from ictbot.settings import CURVE_FILE, STRATEGY_MODE


def build_curve(report: dict) -> list[dict]:
    """Cumulative net_R timeline from a backtest report."""
    closed = [s for s in report["signals"] if s["outcome"] in ("WIN", "LOSS")]
    closed.sort(key=lambda s: s.get("closed_at") or s.get("time"))
    out = []
    running = 0.0
    for s in closed:
        running += s.get("net_R", 0)
        out.append(
            {
                "time": str(s.get("closed_at") or s.get("time")),
                "outcome": s["outcome"],
                "net_R": s.get("net_R", 0),
                "cum_R": round(running, 4),
            }
        )
    return out


def main():
    ap = argparse.ArgumentParser(description="Backtest equity curve writer")
    ap.add_argument("pair")
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
    args = ap.parse_args()

    require_fvg = not args.no_fvg
    # Resolve invert flag: explicit --invert/--follow win, else fall back to
    # STRATEGY_MODE. This is the same precedence the live analyzer uses.
    if args.invert:
        invert = True
    elif args.follow:
        invert = False
    else:
        invert = STRATEGY_MODE == "fade"

    try:
        report = run_backtest(
            args.pair,
            bars=args.bars,
            verbose=False,
            quiet=False,
            poi_tolerance=args.poi_tol,
            sl_frac=args.sl,
            tp_frac=args.tp,
            require_fvg=require_fvg,
            invert=invert,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    curve = build_curve(report)
    closed = [p for p in curve]
    total_R = closed[-1]["cum_R"] if closed else 0
    payload = {
        "pair": args.pair,
        "bars": args.bars,
        "config": {
            "poi_tol": args.poi_tol,
            "sl": args.sl,
            "tp": args.tp,
            "require_fvg": require_fvg,
            "invert": invert,
        },
        "total_R": total_R,
        "n_closed": len(closed),
        "curve": curve,
    }
    with open(CURVE_FILE, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nWrote {len(curve)} closed-trade points to {CURVE_FILE}")
    print(f"Total net R: {total_R:+.2f}")


if __name__ == "__main__":
    main()
