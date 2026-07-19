#!/usr/bin/env bash
# Durable launcher + WATCHDOG for the CMC WebSocket 4h candle streamer (scripts/cmc_stream.py).
# The streamer is the CMC-native candle feed for the contest arm (momentum_cmc): it rolls CMC's live
# ~15s price ticks into 4h OHLC bars in the shared cache. It self-reconnects on WS drops, but a watchdog
# is needed to survive process DEATH or a HANG so candles accrue continuously to the contest week.
#
# This script is IDEMPOTENT — safe to cron every ~5 min:
#   */5 * * * *  "/Users/apple/Desktop/BNB-Hack-CMC/scripts/cmc_stream.sh" >> data/logs/cmc_stream_watchdog.log 2>&1
# If the stream is running AND its heartbeat is fresh, it does nothing. If the process is gone or the
# heartbeat is stale (the stream hung), it kills any stale instance and (re)starts via nohup.
#
# Status:  pgrep -fl cmc_stream.py   ·   tail -f data/logs/cmc_stream.log   ·   stop: pkill -f cmc_stream.py
set -euo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
export PYTHONPATH="$REPO/src"          # settings (CMC_PRO_API_KEY from .env) + the heartbeat path
mkdir -p data/logs
HEARTBEAT="data/logs/cmc_stream_heartbeat.ts"
STALE_S="${CMC_STREAM_STALE_S:-180}"   # heartbeat older than this (s) => the stream hung; restart

heartbeat_fresh() {
  [ -f "$HEARTBEAT" ] || return 1
  local now ts age
  now="$(date +%s)"
  ts="$(( $(cat "$HEARTBEAT" 2>/dev/null || echo 0) / 1000 ))"
  age="$(( now - ts ))"
  [ "$age" -ge 0 ] && [ "$age" -lt "$STALE_S" ]
}

# Healthy already? (process alive AND heartbeat fresh) -> nothing to do.
if pgrep -f 'scripts/cmc_stream.py' >/dev/null 2>&1 && heartbeat_fresh; then
  exit 0
fi

# Dead or hung -> kill any stale instance, then (re)start detached.
pkill -f 'scripts/cmc_stream.py' 2>/dev/null || true
sleep 1
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) (re)starting cmc_stream (dead/stale heartbeat) ===" \
  >> data/logs/cmc_stream_watchdog.log
nohup .venv/bin/python scripts/cmc_stream.py >> data/logs/cmc_stream.log 2>&1 &
echo "started pid $! at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> data/logs/cmc_stream_watchdog.log
