"""
Position sizing calculator.

Modes:
  1. Fixed-fractional risk (default):
       python -m ictbot.engine.sizing --balance 1000 --risk 1 --entry 77000 --sl 76600
  2. Kelly criterion from observed win-rate + reward/risk ratio:
       python -m ictbot.engine.sizing --balance 1000 --kelly --win-rate 50 --rr 3
  3. Auto-size every OPEN journal signal at fixed-fractional risk:
       python -m ictbot.engine.sizing --balance 1000 --risk 1
"""

import argparse

from ictbot.portfolio.journal import read_journal


def position_size(balance: float, risk_pct: float, entry: float, sl: float) -> dict:
    """Return position size details.

    risk_pct is a percentage (1 = 1%), not a fraction.
    Returns dict with:
      - risk_usd: max acceptable loss in USD
      - sl_distance: absolute price distance to SL
      - sl_pct: SL distance as % of entry
      - qty: contract / coin quantity to trade
      - notional: position size in USD
    """
    if balance <= 0:
        raise ValueError("balance must be positive")
    if risk_pct <= 0:
        raise ValueError("risk_pct must be positive")
    if entry <= 0 or sl <= 0:
        raise ValueError("entry and sl must be positive")
    if entry == sl:
        raise ValueError("entry and SL cannot be equal")

    risk_usd = balance * (risk_pct / 100.0)
    sl_distance = abs(entry - sl)
    qty = risk_usd / sl_distance
    return {
        "balance": balance,
        "risk_pct": risk_pct,
        "risk_usd": round(risk_usd, 2),
        "entry": entry,
        "sl": sl,
        "sl_distance": round(sl_distance, 6),
        "sl_pct": round(100.0 * sl_distance / entry, 3),
        "qty": round(qty, 6),
        "notional": round(qty * entry, 2),
    }


def kelly_fraction(win_rate_pct: float, rr: float) -> float:
    """Classic Kelly: f* = p - (1-p)/b
    where p = win prob, b = reward/risk ratio (RR).

    Returns the optimal fraction of bankroll to risk PER TRADE.
    A negative result means no edge → bet 0.
    Half-Kelly (returned separately by `kelly_position_size`) is the
    more conservative practical choice.
    """
    if win_rate_pct < 0 or win_rate_pct > 100:
        raise ValueError("win_rate_pct must be 0..100")
    if rr <= 0:
        raise ValueError("rr must be positive")
    p = win_rate_pct / 100.0
    f = p - (1 - p) / rr
    return round(max(f, 0.0), 6)


def kelly_position_size(
    balance: float,
    win_rate_pct: float,
    rr: float,
    entry: float | None = None,
    sl: float | None = None,
    half: bool = True,
) -> dict:
    """Return Kelly (or half-Kelly) sizing.

    If entry+sl given, also returns the contract qty for the suggested
    risk fraction. Half-Kelly is recommended for live trading because
    full Kelly is brutal on drawdowns when win_rate estimates are noisy.
    """
    full = kelly_fraction(win_rate_pct, rr)
    used = (full / 2.0) if half else full
    out = {
        "balance": balance,
        "win_rate_pct": win_rate_pct,
        "rr": rr,
        "full_kelly_pct": round(full * 100, 3),
        "half_kelly_pct": round((full / 2) * 100, 3),
        "used_kelly_pct": round(used * 100, 3),
        "risk_usd": round(balance * used, 2),
    }
    if entry is not None and sl is not None and entry > 0 and sl != entry:
        sl_distance = abs(entry - sl)
        out["entry"] = entry
        out["sl"] = sl
        out["sl_distance"] = round(sl_distance, 6)
        out["qty"] = round((balance * used) / sl_distance, 6)
        out["notional"] = round(out["qty"] * entry, 2)
    return out


def risk_of_ruin(
    win_rate_pct: float, rr: float, risk_per_trade: float, drawdown_target: float = 0.5
) -> dict:
    """Monte-Carlo simulate to estimate the probability of drawing down
    to `drawdown_target` (fraction of starting balance, default 50%)
    given a fixed-fractional bet of `risk_per_trade` (fraction of equity).

    Uses 10k paths of 500 trades each.
    """
    import random

    if not (0 <= win_rate_pct <= 100):
        raise ValueError("win_rate_pct must be 0..100")
    if risk_per_trade <= 0 or risk_per_trade >= 1:
        raise ValueError("risk_per_trade must be 0..1 (exclusive)")
    if rr <= 0:
        raise ValueError("rr must be positive")

    p = win_rate_pct / 100.0
    n_paths = 10000
    n_trades = 500
    ruin_floor = drawdown_target

    rng = random.Random(42)  # deterministic for reproducibility
    ruined = 0
    final_balances = []
    for _ in range(n_paths):
        equity = 1.0
        peak = 1.0
        hit_floor = False
        for _ in range(n_trades):
            risk_amt = equity * risk_per_trade
            if rng.random() < p:
                equity += risk_amt * rr
            else:
                equity -= risk_amt
            peak = max(peak, equity)
            if equity / peak <= 1 - ruin_floor:
                hit_floor = True
                break
        if hit_floor:
            ruined += 1
        final_balances.append(equity)

    return {
        "win_rate_pct": win_rate_pct,
        "rr": rr,
        "risk_per_trade_pct": round(risk_per_trade * 100, 3),
        "drawdown_target_pct": round(drawdown_target * 100, 1),
        "n_paths": n_paths,
        "n_trades_per_path": n_trades,
        "ruin_probability_pct": round(100 * ruined / n_paths, 2),
        "median_final_x": round(sorted(final_balances)[n_paths // 2], 3),
        "best_final_x": round(max(final_balances), 3),
        "worst_final_x": round(min(final_balances), 3),
    }


def _print(d: dict) -> None:
    print(f"  entry            : {d['entry']}")
    print(f"  stop loss        : {d['sl']}")
    print(f"  SL distance      : {d['sl_distance']} ({d['sl_pct']}%)")
    print(f"  max risk         : ${d['risk_usd']} ({d['risk_pct']}% of ${d['balance']})")
    print(f"  position qty     : {d['qty']}")
    print(f"  notional value   : ${d['notional']}")


def _print_kelly(d: dict) -> None:
    print(f"  win-rate         : {d['win_rate_pct']}%")
    print(f"  reward/risk      : {d['rr']}")
    print(f"  full Kelly       : {d['full_kelly_pct']}% of balance per trade")
    print(f"  half Kelly       : {d['half_kelly_pct']}% of balance per trade  (recommended)")
    print(f"  using            : {d['used_kelly_pct']}% → ${d['risk_usd']} at risk")
    if "qty" in d:
        print(f"  entry            : {d['entry']}")
        print(f"  stop loss        : {d['sl']}")
        print(f"  SL distance      : {d['sl_distance']}")
        print(f"  position qty     : {d['qty']}")
        print(f"  notional value   : ${d['notional']}")


def main():
    ap = argparse.ArgumentParser(description="Position size calculator")
    ap.add_argument("--balance", type=float, required=True, help="Account balance in USD")
    ap.add_argument(
        "--risk",
        type=float,
        default=None,
        help="Fixed-fractional risk per trade as percent (e.g. 1 = 1%%)",
    )
    ap.add_argument(
        "--entry",
        type=float,
        default=None,
        help="Entry price (omit to size every OPEN journal signal)",
    )
    ap.add_argument("--sl", type=float, default=None, help="Stop-loss price")
    # Kelly mode
    ap.add_argument("--kelly", action="store_true", help="Use Kelly criterion sizing")
    ap.add_argument(
        "--full-kelly",
        action="store_true",
        help="Use full Kelly instead of half Kelly (brutal on drawdowns)",
    )
    ap.add_argument(
        "--win-rate", type=float, default=None, help="Observed win-rate as percent (for --kelly)"
    )
    ap.add_argument(
        "--rr", type=float, default=None, help="Observed reward/risk ratio (for --kelly or --ror)"
    )
    # Risk-of-ruin mode
    ap.add_argument(
        "--ror", action="store_true", help="Compute risk of ruin (Monte Carlo simulation)"
    )
    ap.add_argument(
        "--drawdown",
        type=float,
        default=50,
        help="Drawdown threshold for ruin, as percent (default 50)",
    )
    args = ap.parse_args()

    if args.kelly:
        if args.win_rate is None or args.rr is None:
            ap.error("--kelly requires --win-rate and --rr")
        d = kelly_position_size(
            args.balance,
            args.win_rate,
            args.rr,
            entry=args.entry,
            sl=args.sl,
            half=not args.full_kelly,
        )
        label = "FULL KELLY" if args.full_kelly else "HALF KELLY"
        print(f"\n{label} SIZING\n" + "=" * 40)
        _print_kelly(d)
        if d["full_kelly_pct"] == 0:
            print("\n  ⚠️  Kelly is 0 — no edge. Don't trade.")

        # Always show risk-of-ruin alongside the suggested Kelly fraction.
        if d["used_kelly_pct"] > 0:
            ror = risk_of_ruin(
                args.win_rate, args.rr, d["used_kelly_pct"] / 100.0, drawdown_target=0.5
            )
            print("\n  RISK OF RUIN (50% drawdown)")
            print(f"    over 500 trades: {ror['ruin_probability_pct']}%")
            print(f"    median outcome : {ror['median_final_x']}× starting balance")
            print(f"    worst case     : {ror['worst_final_x']}× starting balance")
        print()
        return

    if args.ror:
        if args.win_rate is None or args.rr is None or args.risk is None:
            ap.error("--ror requires --win-rate, --rr, and --risk")
        ror = risk_of_ruin(
            args.win_rate, args.rr, args.risk / 100.0, drawdown_target=args.drawdown / 100.0
        )
        print("\nRISK OF RUIN\n" + "=" * 40)
        print(f"  win-rate            : {ror['win_rate_pct']}%")
        print(f"  reward/risk         : {ror['rr']}")
        print(f"  risk per trade      : {ror['risk_per_trade_pct']}% of balance")
        print(f"  drawdown threshold  : {ror['drawdown_target_pct']}%")
        print(
            f"  ruin probability    : {ror['ruin_probability_pct']}% "
            f"over {ror['n_trades_per_path']} trades"
        )
        print(f"  median final equity : {ror['median_final_x']}× starting")
        print(f"  worst final equity  : {ror['worst_final_x']}× starting")
        print(f"  best final equity   : {ror['best_final_x']}× starting")
        print()
        return

    if args.risk is None:
        ap.error("--risk is required (unless using --kelly or --ror)")

    if args.entry is not None and args.sl is not None:
        d = position_size(args.balance, args.risk, args.entry, args.sl)
        print("\nPOSITION SIZE\n" + "=" * 40)
        _print(d)
        print()
        return

    # No entry/sl given — size every OPEN signal in the journal.
    open_signals = [e for e in read_journal() if e["outcome"] == "OPEN"]
    if not open_signals:
        print("\nNo OPEN signals in the journal — pass --entry and --sl to size manually.\n")
        return

    print(
        f"\nSizing {len(open_signals)} OPEN signal(s) @ balance=${args.balance}, risk={args.risk}%:"
    )
    print("=" * 60)
    for sig in open_signals:
        d = position_size(args.balance, args.risk, sig["price"], sig["sl"])
        print(f"\n{sig['pair']} ({sig['entry']})")
        _print(d)
    print()


if __name__ == "__main__":
    main()
