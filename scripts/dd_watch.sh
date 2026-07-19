#!/usr/bin/env bash
# FAST intraday risk watcher: drawdown halt + profit-lock ratchet.
# Mode argument: `dd_watch.sh sim` (campaign paper track) or `dd_watch.sh live`
# (contest week; default live for backwards compatibility).
#
# Reaction-time safety (audit finding G): the scheduled tick checks risk only at
# rebalance cadence, so an intraday NAV crash toward the DQ line — or a profit
# spike past the lock threshold — could go unreacted-to for hours. This runs
# every ~15 min and is strictly ONE-DIRECTIONAL: on a drawdown breach it sells
# token->USDT and halts; on a profit-lock trigger it arms/banks the ratchet
# (also flatten-only). It NEVER opens, flips, or rebalances, so it adds
# protection with zero overtrading risk. It shares the tick's per-mode Python
# lock, so the two can never run against the book at once.
#
# Campaign cron (sim):   */15 * * * *  ".../scripts/dd_watch.sh" sim
# Contest cron (live):   */10 * 22-28 6 *  ".../scripts/dd_watch.sh" live
# Watch:  tail -f data/logs/dd_watch_<mode>.log   ·   stop: crontab -e (remove the line)
set -euo pipefail

MODE="${1:-live}"
REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
# node v26 first so BOTH `twak` (needs Node >= 22) and `node` resolve correctly.
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src"          # shadow any stale editable .pth
mkdir -p data/logs
LOG="data/logs/dd_watch_${MODE}.log"

# Overlap guard: prefer flock (Linux). The Python monitor ALSO self-locks (the SAME
# per-mode lock the scheduled tick uses, returning a skip code if either is running),
# so this just avoids spawning a duplicate monitor; degrades gracefully where flock
# is absent.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.dd_watch_${MODE}.flock"
  if ! flock -n 9; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) SKIP: prior dd-watch still running ===" >> "$LOG"
    exit 0
  fi
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) dd-watch ($MODE) ===" >> "$LOG"
set +e
.venv/bin/python scripts/run_allocator.py --mode "$MODE" --dd-watch >> "$LOG" 2>&1
rc=$?
set -e
# LIVE only: periodic re-push of the dashboard snapshot (every ~30 min) so the deployed override stays
# warm across free-tier cold-starts + the regime/F&G cards refresh between daily ticks (best-effort).
if [ "$MODE" = "live" ]; then bash "$REPO/scripts/publish_snapshot.sh" >> "$LOG" 2>&1 || true; fi
exit $rc
