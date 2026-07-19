#!/usr/bin/env bash
# Contest >=1-trade/DAY floor (the brief's other minimum, alongside >=7/week).
# Cron'd once near end-of-day UTC during the contest window ONLY: if the UTC day
# would otherwise end with zero successful swaps, run_allocator banks ONE
# ~0-NAV-impact round-trip nudge. Requires TRADE_FLOOR_DAILY=true in .env —
# otherwise the Python side no-ops, so an early cron install is harmless.
#
# Cron (local IST = UTC+05:30, so 22:10 UTC = 03:40 IST the NEXT day — hence
# the day-of-month range 23-29 for the Jun 22-28 window):
#   40 3 23-29 6 *  "/Users/apple/Desktop/BNB-Hack-CMC/scripts/daily_floor.sh" live
# Watch:  tail -f data/logs/daily_floor_<mode>.log
set -euo pipefail

MODE="${1:-live}"
REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
# node v26 first so BOTH `twak` (needs Node >= 22) and `node` resolve correctly.
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src"          # shadow any stale editable .pth
mkdir -p data/logs
LOG="data/logs/daily_floor_${MODE}.log"

if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.daily_floor_${MODE}.flock"
  if ! flock -n 9; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) SKIP: prior daily-floor still running ===" >> "$LOG"
    exit 0
  fi
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) daily-floor ($MODE) ===" >> "$LOG"
.venv/bin/python scripts/run_allocator.py --mode "$MODE" --ensure-daily-floor >> "$LOG" 2>&1
