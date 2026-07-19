#!/usr/bin/env bash
# A/B race daemon — track mean_reversion + momentum_cmc head-to-head on live CMC and AUTO-LOCK the live arm
# to the leader when the signal sustains. Supervised while-loop (mirrors auto_trader.sh): each cycle runs
# scripts/ab_race.py once = a LIVE tick on the current arm (real, TWAK auto-exec) + a SHADOW paper tick on
# the other arm (isolated, zero spend) + compare + maybe flip STRATEGY_NAME. Keys stay in local .env.
#
# Run (detached, cron-independent):
#   nohup bash scripts/ab_race.sh >> data/logs/ab_race.log 2>&1 &
#   tail -f data/logs/ab_race.log
#   touch data/KILL_SWITCH_ENGAGED      # halt trading next tick (race keeps tracking)
#   pkill -f scripts/ab_race.sh         # stop the race daemon
#
# Tunables (env): AB_INTERVAL_S (3600=hourly) · AB_MARGIN (0.01) · AB_SUSTAIN (2) · AB_MAX_CYCLES (0=forever)
#   AB_PAPER=1 → smoke mode: the LIVE tick runs --quote-only (zero spend).
set -uo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src"
export CMC_ONLY=true
mkdir -p data/logs data/ab

INTERVAL_S="${AB_INTERVAL_S:-3600}"
MAX_CYCLES="${AB_MAX_CYCLES:-0}"
LOG="data/logs/ab_race.log"
HEARTBEAT="data/logs/ab_race_heartbeat.ts"
PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY="python3"
PYFLAGS=""; [ "${AB_PAPER:-0}" = "1" ] && PYFLAGS="--paper --dry-lock"   # smoke: quote-only live tick + never flip .env

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$1" | tee -a "$LOG"; }

if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.ab_race.flock"
  if ! flock -n 9; then log "=== $(ts) SKIP: an ab_race daemon is already running ==="; exit 0; fi
fi
trap 'log "=== $(ts) ab_race STOP (signal) after $cycle cycles ==="; exit 0' INT TERM

log "=== $(ts) ab_race START (interval=${INTERVAL_S}s margin=${AB_MARGIN:-0.01} sustain=${AB_SUSTAIN:-2} max=${MAX_CYCLES} paper=${AB_PAPER:-0}) ==="
cycle=0
while true; do
  cycle=$((cycle + 1))
  log "=== $(ts) cycle $cycle ==="
  "$PY" scripts/ab_race.py $PYFLAGS >> "$LOG" 2>&1 || log "  cycle $cycle: ab_race.py rc=$?"
  bash "$REPO/scripts/publish_snapshot.sh" >> "$LOG" 2>&1 || true
  echo "$(date +%s)000" > "$HEARTBEAT"
  if [ "$MAX_CYCLES" -gt 0 ] && [ "$cycle" -ge "$MAX_CYCLES" ]; then
    log "=== $(ts) ab_race DONE ($cycle/$MAX_CYCLES cycles) ==="
    break
  fi
  sleep "$INTERVAL_S"
done
