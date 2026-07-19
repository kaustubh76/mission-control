#!/usr/bin/env bash
# Daily LIVE rebalance tick for the BNB Hack contest week (2026-06-22 -> 06-28).
# The autonomous agent: reads CMC (price + Fear&Greed) -> regime-adaptive momentum
# decision -> executes the rebalance via TWAK (native-gas swaps) -> journals the
# natural-language rationale. This is the contest deployment; pre-contest, keep using
# forward_tick.sh (SIM) so the small live balance isn't churned.
#
# Cron it ONLY for the contest window, e.g.:
#   7 13 22-28 6 *  "/Users/apple/Desktop/BNB-Hack-CMC/scripts/live_tick.sh"
# Watch:  tail -f data/logs/allocator_live.log   ·   stop: crontab -e (remove the line)
#
# Optional arm switch: pass a registered arm as $1 to run it live instead of the .env default
# (e.g. `live_tick.sh mean_reversion`). run_allocator's survival gate REFUSES a DQ-unsafe arm and
# WARNS on a forward-not-eligible one. Pre-check with `python scripts/arm_check.py --arm <name>`.
#
# Reaction-time safety: pair this daily tick with scripts/dd_watch.sh — a FAST,
# flatten-only intraday drawdown monitor (cron every ~10 min in the same window). It
# shares this tick's per-mode lock and only ever flattens, so it bounds intraday
# drawdown toward the 30% DQ line without any overtrading risk. See dd_watch.sh.
set -euo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
ARM="${1:-}"   # optional live arm (e.g. mean_reversion); empty = .env STRATEGY_NAME (momentum_cmc)
# node v26 first so BOTH `twak` (needs Node >= 22) and `node` resolve correctly.
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src"          # shadow the stale editable .pth (loads the wrong tree)
# Zero-CEX firewall: the contest arm (STRATEGY_NAME=momentum_cmc) decides + sizes ENTIRELY on
# CoinMarketCap data; any Binance/Bybit reach RAISES instead of silently using exchange data. The
# LIVE contest tick runs only the CMC arm, so the firewall is always correct here.
export CMC_ONLY=true
mkdir -p data/logs

# Overlap guard: prefer flock (Linux). The Python tick ALSO self-locks (returns a
# skip code if one is already running), so this just avoids spawning a duplicate
# process; it degrades gracefully where flock is absent (stock macOS).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.live_tick.flock"
  if ! flock -n 9; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) SKIP: prior LIVE tick still running ===" >> data/logs/allocator_live.log
    exit 0
  fi
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) LIVE tick${ARM:+ (arm=$ARM)} ===" >> data/logs/allocator_live.log
set +e
.venv/bin/python scripts/run_allocator.py --mode live ${ARM:+--strategy "$ARM"} >> data/logs/allocator_live.log 2>&1
rc=$?
set -e
# Push the fresh snapshot to the live dashboard so it reflects THIS rebalance within ~4s (best-effort;
# runs whether the tick rebalanced, halted, or skipped — and never changes the tick's exit code).
bash "$REPO/scripts/publish_snapshot.sh" >> data/logs/allocator_live.log 2>&1 || true
exit $rc
