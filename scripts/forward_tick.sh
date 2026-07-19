#!/usr/bin/env bash
# Forward paper-trading tick for the BNB momentum allocator (campaign cadence).
# Installed in crontab ~10 min after each 4h UTC bar close (now -> contest
# 2026-06-22), building a FORWARD track record on unseen data. SIM mode is
# forced — this NEVER places a real swap, regardless of .env. Output appends
# to data/logs/allocator_cron.log.
#
# Config: ALL campaign levers (CMC intel/TA/MCP/skill, PROFIT_LOCK_*, the cap
# band) live in .env — the single source of truth shared with the dashboard's
# /controls sim-tick and the dd_watch.sh watcher, so every entry point trades
# the same config. Only the 10% campaign drawdown cap is pinned here (defense
# in depth alongside MAX_DRAWDOWN_FRAC=0.10 in .env).
#
# Verify:  make forward_report        Remove:  crontab -e  (delete the bnb-campaign lines)
set -euo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
export PYTHONPATH="$REPO/src"          # shadow any stale editable .pth
# Zero-CEX firewall for the CMC-native contest arm (STRATEGY_NAME=momentum_cmc in .env): any reach
# into a Binance/Bybit path RAISES instead of silently serving exchange data. Set HERE (not in .env)
# so the separate forward_tracks.sh — which forward-tests the legacy CEX research arms — is unaffected.
export CMC_ONLY=true
mkdir -p data/logs

# Overlap guard (see live_tick.sh) — the Python tick self-locks too.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.forward_tick.flock"
  if ! flock -n 9; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) SKIP: prior forward tick still running ===" >> data/logs/allocator_cron.log
    exit 0
  fi
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) forward tick ===" >> data/logs/allocator_cron.log
.venv/bin/python scripts/run_allocator.py --mode sim --dd-cap 0.10 >> data/logs/allocator_cron.log 2>&1
