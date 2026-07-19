"""
Inspect the live signal journal.

USAGE:
  python -m ictbot.cli.journal_cmd                              # show recent + summary
  python -m ictbot.cli.journal_cmd --pair BTC/USDT:USDT         # filter by pair
  python -m ictbot.cli.journal_cmd --limit 50                   # show last 50
  python -m ictbot.cli.journal_cmd --open                       # only OPEN signals
"""

import argparse

from ictbot.portfolio.journal import read_journal, score_journal


def main():
    ap = argparse.ArgumentParser(description="Show signal journal")
    ap.add_argument("--pair", help="Filter to one pair")
    ap.add_argument("--limit", type=int, default=20, help="Show last N (default 20)")
    ap.add_argument("--open", action="store_true", help="Only show OPEN signals")
    args = ap.parse_args()

    entries = read_journal(pair=args.pair)
    if args.open:
        entries = [e for e in entries if e["outcome"] == "OPEN"]

    stats = score_journal(entries)

    print()
    print("=" * 78)
    title = "JOURNAL"
    if args.pair:
        title += f" — {args.pair}"
    if args.open:
        title += " (OPEN only)"
    print(title)
    print("=" * 78)
    print(
        f"Total : {stats['total']}   WIN : {stats['wins']}   "
        f"LOSS : {stats['losses']}   OPEN : {stats['open']}",
        end="",
    )
    if stats["win_rate"] is not None:
        print(f"   win-rate (closed) : {stats['win_rate']:.1f}%")
    else:
        print()
    print()

    if not entries:
        print("(no signals yet — run `make scan` to populate)")
        return

    for e in entries[-args.limit :]:
        marker = {"WIN": "✓", "LOSS": "✗", "OPEN": "·"}[e["outcome"]]
        print(
            f" {marker}  {e['ts'][:19]}  {e['pair']:<18} "
            f"{e['entry']:<4} @ {e['price']:<10} "
            f"SL={e['sl']:<10} TP={e['tp']:<10} "
            f"RR=1:{e['rr']}  conf={e['confidence']}%  "
            f"→ {e['outcome']}"
        )
    print()


if __name__ == "__main__":
    main()
