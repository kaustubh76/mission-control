#!/usr/bin/env bash
# Forward-gated strategy auto-selector — cron wrapper. Runs AFTER the forward sweep (forward_tracks.sh
# at 43 6,18) so it scores on fresh forward data, then drives the SIM selector + writes the LIVE
# recommendation. Recommend-only for live by default (never touches the real-money arm on its own).
#
# Cron (every 12h, just after the forward tracks):
#   50 6,18 * * *  "<repo>/scripts/strategy_evaluator.sh"   # bnb-strategy-evaluator
# Watch:  tail -f data/logs/strategy_evaluator.log
set -euo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src:$REPO"     # src shadows stale .pth; repo root makes `scripts.*` importable
mkdir -p data/logs
LOG="data/logs/strategy_evaluator.log"

# Overlap guard (idempotent decision; flock just avoids a duplicate run).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.strategy_evaluator.flock"
  if ! flock -n 9; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) SKIP: prior evaluator still running ===" >> "$LOG"
    exit 0
  fi
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) strategy_evaluator ===" >> "$LOG"
.venv/bin/python scripts/strategy_evaluator.py >> "$LOG" 2>&1
