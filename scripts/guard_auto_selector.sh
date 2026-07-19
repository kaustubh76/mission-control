#!/usr/bin/env bash
# Auto-selector health GUARD — ~1-hour verify + keep-fresh loop (12 cycles × 5 min by default).
# Each cycle re-proves the engine (isolated smoke), ticks the REAL evaluator to refresh the live
# recommendation (recommend-only — never changes the live arm), validates the live files, and best-effort
# checks the deployed API. Emits one heartbeat line per cycle + a final SUMMARY; exits non-zero if any
# cycle hard-failed.
#
# OPERATOR-RUN, NOT cron (editing a cron script re-breaks its exec via com.apple.provenance/EPERM).
#   Foreground:  bash scripts/guard_auto_selector.sh
#   Background:  nohup bash scripts/guard_auto_selector.sh >/dev/null 2>&1 &   (watch the log)
#   Watch:       tail -f data/logs/auto_selector_guard.log
#   Tunables:    GUARD_CYCLES=12 GUARD_INTERVAL_S=300 GUARD_FLAGS="--no-tick --no-api"
set -euo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src:$REPO"     # src shadows stale .pth; repo root makes `scripts.*` importable
# NOTE: deliberately do NOT export ALLOCATOR_DATA_DIR — the tick + validation must hit the LIVE data dir.
mkdir -p data/logs

CYCLES="${GUARD_CYCLES:-12}"
INTERVAL_S="${GUARD_INTERVAL_S:-300}"
FLAGS="${GUARD_FLAGS:-}"
LOG="data/logs/auto_selector_guard.log"
HEARTBEAT="data/logs/auto_selector_guard_heartbeat.ts"
PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY="python3"

# Overlap guard — one guard loop at a time.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.auto_selector_guard.flock"
  if ! flock -n 9; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) SKIP: a guard loop is already running ===" | tee -a "$LOG"
    exit 0
  fi
fi

log() { echo "$1" | tee -a "$LOG"; }

log "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) auto_selector_guard START (cycles=$CYCLES interval=${INTERVAL_S}s flags='${FLAGS}') ==="
pass=0; fail=0; last=""
for i in $(seq 1 "$CYCLES"); do
  # Capture the heartbeat line; tee it to the log + console. Don't let a non-zero pass abort the loop.
  line="$("$PY" scripts/guard_auto_selector.py $FLAGS 2>&1)" && rc=0 || rc=$?
  log "  cycle $i/$CYCLES  $line"
  last="$line"
  if [ "$rc" -eq 0 ]; then pass=$((pass + 1)); else fail=$((fail + 1)); fi
  echo "$(date +%s)000" > "$HEARTBEAT"
  if [ "$i" -lt "$CYCLES" ]; then sleep "$INTERVAL_S"; fi
done

log "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) auto_selector_guard SUMMARY: $pass/$CYCLES PASS, $fail FAIL · last: ${last#\[*\] } ==="
[ "$fail" -eq 0 ] || exit 1
