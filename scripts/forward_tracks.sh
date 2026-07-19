#!/usr/bin/env bash
# Forward paper-trading ticks for the CHALLENGER arms, each in its OWN isolated data tree
# (data/forward/<arm>/) via ALLOCATOR_DATA_DIR — so they accrue a real wall-clock FORWARD track
# on unseen data WITHOUT touching the production momentum_adaptive journal (data/journal/) that the
# dashboard displays (forward_tick.sh owns that). SIM mode is forced — this NEVER places a real swap.
#
# Cron (every 12h, ~off-minute):
#   43 6,18 * * *  "<repo>/scripts/forward_tracks.sh"  # bnb-forward-tracks
# Read:   make forward_track_report ARM=dual_momentum   |   make readiness
# Remove: crontab -e  (delete the bnb-forward-tracks line)
set -euo pipefail
REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
export PYTHONPATH="$REPO/src"            # shadow any stale editable .pth
mkdir -p data/logs
LOG="data/logs/forward_tracks.log"
ARMS="${FORWARD_ARMS:-dual_momentum breakout momentum_voltarget mean_reversion rotation momentum_fast grid momentum_mafilter}"

# Overlap guard — the Python tick self-locks per data tree too.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.forward_tracks.flock"
  if ! flock -n 9; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) SKIP: prior forward-tracks tick still running ===" >> "$LOG"
    exit 0
  fi
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) forward-tracks tick ($ARMS) ===" >> "$LOG"
for arm in $ARMS; do
  mkdir -p "data/forward/$arm/journal"
  echo "--- $arm ---" >> "$LOG"
  ALLOCATOR_DATA_DIR="data/forward/$arm" STRATEGY_NAME="$arm" \
    .venv/bin/python scripts/run_allocator.py --mode sim --dd-cap 0.10 >> "$LOG" 2>&1 || \
    echo "tick failed for $arm (continuing)" >> "$LOG"
done
