"""
Archive data/journal/signals.json with a UTC datestamp suffix and
recreate an empty journal. Used to start a clean slate after the Fix
2.A-2.F live-P&L plumbing change.

Usage:
    python scripts/archive_journal.py
    python scripts/archive_journal.py --suffix manual    # custom suffix

After archival the new signals.json is just `[]`. The archived file
remains queryable via the diagnostic CLI:

    python scripts/diagnose_live_pnl.py \
        --journal data/journal/signals_pre_fix_2026-06-05.json
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = REPO_ROOT / "data" / "journal" / "signals.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive the live signals journal.")
    ap.add_argument("--journal", default=str(JOURNAL_PATH),
                    help=f"Journal file to archive (default: {JOURNAL_PATH})")
    ap.add_argument("--suffix", default=None,
                    help="Override the datestamp suffix (e.g. 'manual'). "
                         "Default is today's UTC date.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would happen; do not move files.")
    args = ap.parse_args()

    journal = Path(args.journal)
    if not journal.exists():
        print(f"Nothing to archive: {journal} does not exist", file=sys.stderr)
        return 0
    if journal.stat().st_size <= 3:
        print(f"Nothing to archive: {journal} is already empty")
        return 0

    suffix = args.suffix or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive = journal.with_name(f"signals_pre_fix_{suffix}.json")

    # Avoid clobbering an earlier archive — append a counter if needed.
    counter = 1
    while archive.exists():
        archive = journal.with_name(f"signals_pre_fix_{suffix}_{counter}.json")
        counter += 1

    print(f"archive: {journal} -> {archive}")
    if args.dry_run:
        print("dry-run: no file system change")
        return 0

    shutil.move(str(journal), str(archive))
    journal.write_text("[]\n")
    print(f"recreated empty: {journal} (size={journal.stat().st_size} bytes)")
    print(f"queryable via:   python scripts/diagnose_live_pnl.py --journal {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
