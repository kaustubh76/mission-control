#!/usr/bin/env bash
# Publish the current LIVE dashboard snapshot to the deployed (read-only) API so the public dashboard
# reflects the latest rebalance within one SPA poll (~4s) — NO git push / image rebuild. Best-effort:
# it NEVER fails its caller (the live trading tick). Called at the end of live_tick.sh (immediate,
# post-rebalance) and dd_watch.sh live (periodic re-push — keeps the ephemeral override warm across
# free-tier cold-starts and refreshes the regime/F&G/x402 live cards between daily ticks).
#
# Auth: INGEST_TOKEN from the env, else read JUST that one key from the gitignored .env (never echoed).
# No token => no-op (logged). Target via $DASHBOARD_API (default: canonical Render URL).
set -uo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 0
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src"
mkdir -p data/logs
LOG="data/logs/publish_snapshot.log"
API="${DASHBOARD_API:-https://bnb-mission-control-api.onrender.com}"

# INGEST_TOKEN: prefer the env, else extract ONLY that key from .env (never print its value).
TOKEN="${INGEST_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$REPO/.env" ]; then
  TOKEN="$(grep -E '^INGEST_TOKEN=' "$REPO/.env" | head -1 | cut -d= -f2-)"
fi
if [ -z "$TOKEN" ]; then
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) publish SKIP: no INGEST_TOKEN set ===" >> "$LOG"
  exit 0
fi

# Overlap guard so a tick + the 30-min watcher don't race the export.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.publish_snapshot.flock"
  flock -n 9 || { echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) publish SKIP: prior publish running ===" >> "$LOG"; exit 0; }
fi

# 1) regenerate the LIVE snapshot (DASHBOARD_JOURNAL=live overrides the local sim default).
if ! DASHBOARD_JOURNAL=live .venv/bin/python scripts/export_snapshot.py >> "$LOG" 2>&1; then
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) publish FAIL: export_snapshot errored ===" >> "$LOG"
  exit 0
fi

# 2) push it to the live dashboard API (best-effort; log the HTTP code, never the token).
code=$(curl -s -o /dev/null -w '%{http_code}' -m 25 -X POST \
  -H "X-Ingest-Token: $TOKEN" -H "Content-Type: application/json" \
  --data @web/public/snapshot.json "$API/api/ingest/snapshot" 2>>"$LOG" || echo "000")
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) publish -> $API/api/ingest/snapshot http=$code ===" >> "$LOG"
exit 0
