#!/usr/bin/env bash
# Hands-off LOCAL auto-trader daemon — the strategy evaluates LIVE CMC conditions on a cadence and TWAK
# auto-executes the swaps when conditions cross the rebalance band. Supervised while-loop (mirrors
# cmc_stream.sh + guard_auto_selector.sh): each cycle runs ONE FRESH `run_allocator` process, so a hung or
# crashed tick can NEVER kill the daemon (the next cycle starts clean). Keys stay in the LOCAL .env — this
# is the WRITE tier; it is NEVER deployed (zero secrets on Render, per render.yaml).
#
# Cron-independent by design (the macOS cron is blocked by provenance/FDA). Run it from your session:
#   nohup bash scripts/auto_trader.sh >> data/logs/auto_trader.log 2>&1 &
#   tail -f data/logs/auto_trader.log              # watch
#   touch data/KILL_SWITCH_ENGAGED                 # halt trading on the next tick (daemon keeps running)
#   pkill -f scripts/auto_trader.sh                # stop the daemon entirely
# `com.bnb.caffeinate` (already loaded) keeps the Mac awake. Durable upgrade: a launchd agent (needs
# /bin/bash Full Disk Access once) — the com.bnb.* plists already exist.
#
# Every live tick is gated by run_allocator's full safety stack: kill switch · preflight (creds +
# ENABLE_LIVE_TRADING) · arm survival gate · 5% drawdown halt + emergency-flatten · profit-lock ratchet ·
# stale-candle / bad-price skip. rc=1 halts (state stays halted → no further trading until cleared); rc=2
# skips this tick and the loop continues.
#
# Tunables (env):
#   AUTO_TRADER_INTERVAL_S   seconds between ticks         (default 3600 = hourly)
#   AUTO_TRADER_FLAGS        run_allocator flags           (default "--mode live"; smoke: "--quote-only")
#   AUTO_TRADER_MAX_CYCLES   stop after N cycles, 0=forever (default 0; smoke: 2)
set -uo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
# node v26 first so BOTH `twak` (Node >= 22) and `node` resolve; then src on the path; then the CEX firewall.
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src"
export CMC_ONLY=true
# NOTE: deliberately do NOT export ALLOCATOR_DATA_DIR — the live trader must hit the real data dir.
mkdir -p data/logs

INTERVAL_S="${AUTO_TRADER_INTERVAL_S:-3600}"
FLAGS="${AUTO_TRADER_FLAGS:---mode live}"
MAX_CYCLES="${AUTO_TRADER_MAX_CYCLES:-0}"
LOG="data/logs/auto_trader.log"
HEARTBEAT="data/logs/auto_trader_heartbeat.ts"
KILL="data/KILL_SWITCH_ENGAGED"
PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY="python3"
# Which journal the summary reads (live vs dryrun) — derive from the flags.
case "$FLAGS" in *quote-only*|*dryrun*) JRNL="data/journal/allocator_dryrun.jsonl";; *) JRNL="data/journal/allocator_live.jsonl";; esac

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$1" | tee -a "$LOG"; }

# Single-instance guard for the whole daemon lifetime (not per-cycle).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.auto_trader.flock"
  if ! flock -n 9; then
    log "=== $(ts) SKIP: an auto_trader daemon is already running ==="
    exit 0
  fi
fi

trap 'log "=== $(ts) auto_trader STOP (signal) after $cycle cycles ==="; exit 0' INT TERM

# Compact post-tick summary (NAV · n_swaps · cumulative) from the last REBALANCE row — never fatal.
summary() {
  "$PY" - "$JRNL" <<'PY' 2>/dev/null || echo "summary-unavailable"
import json, sys
try:
    rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
    reb = [r for r in rows if str(r.get("event", "")).startswith("REBALANCE")]  # REBALANCE | REBALANCE_DRYRUN
    r = reb[-1]
    print(f"NAV={r.get('nav_after')} n_swaps={r.get('n_swaps')} cum={r.get('cumulative_swaps')} "
          f"F&G={r.get('fear_greed')} cap={r.get('deploy_cap')}")
except Exception:
    print("no-rebalance-row")
PY
}

log "=== $(ts) auto_trader START (interval=${INTERVAL_S}s flags='${FLAGS}' max_cycles=${MAX_CYCLES}) ==="
cycle=0
while true; do
  cycle=$((cycle + 1))
  if [ -f "$KILL" ]; then
    log "  cycle $cycle  $(ts)  KILL SWITCH engaged ($KILL) — skipping tick (no trade)"
  else
    log "=== $(ts) cycle $cycle: run_allocator $FLAGS ==="
    set +e
    "$PY" scripts/run_allocator.py $FLAGS >> "$LOG" 2>&1
    rc=$?
    set -e 2>/dev/null || true
    # Best-effort dashboard push (never changes anything, never fatal).
    bash "$REPO/scripts/publish_snapshot.sh" >> "$LOG" 2>&1 || true
    log "  cycle $cycle  $(ts)  rc=$rc  $(summary)"
    [ "$rc" = "1" ] && log "  ⚠️  cycle $cycle: HALT (rc=1 — drawdown/profit-lock). State stays halted; daemon keeps running but will not trade until cleared."
  fi
  echo "$(date +%s)000" > "$HEARTBEAT"
  if [ "$MAX_CYCLES" -gt 0 ] && [ "$cycle" -ge "$MAX_CYCLES" ]; then
    log "=== $(ts) auto_trader DONE ($cycle/$MAX_CYCLES cycles) ==="
    break
  fi
  sleep "$INTERVAL_S"
done
