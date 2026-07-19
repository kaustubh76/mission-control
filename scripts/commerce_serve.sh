#!/usr/bin/env bash
# ERC-8183 commerce PROVIDER daemon — the agent runs unattended and SELLS its live Market Regime Report to
# other agents' FUNDED jobs. Supervised while-loop (mirrors auto_trader.sh + ab_race.sh): each cycle runs
# ONE FRESH `erc8183_serve.py --once` process, so a hung/crashed asyncio watcher can NEVER kill the daemon
# (the next cycle starts clean). Turns the 2-job demo into an always-on "open for business" provider.
#
# Polling for FUNDED jobs is READ-ONLY on-chain (0 gas); only an actual job submission costs gas
# (gasless on testnet via MegaFuel). Idle polling with no buyer is free — that is the desired state.
# Keys stay in the LOCAL .env (provider keystore = AGENT_WALLET_PASSWORD); NEVER deployed (zero secrets
# on Render). Settlement of SETTLE_DEFERRED jobs is a SEPARATE buyer-side step (`make commerce_settle`).
#
# Cron-independent by design (the macOS cron is blocked by provenance/FDA). Run it from your session:
#   make commerce_serve_check                                       # preflight: provider addr + pending
#   nohup bash scripts/commerce_serve.sh >> data/logs/commerce_serve.log 2>&1 &
#   tail -f data/logs/commerce_serve.log                            # watch
#   touch data/KILL_SWITCH_ENGAGED                                  # pause submits next cycle (daemon lives)
#   pkill -f scripts/commerce_serve.sh                              # stop the daemon entirely
#
# Tunables (env):
#   COMMERCE_INTERVAL_S   seconds between poll cycles    (default 60)
#   ERC8183_NETWORK       bsc-testnet | bsc-mainnet      (default bsc-mainnet)
#   COMMERCE_MAX_CYCLES   stop after N cycles, 0=forever (default 0; smoke: 2)
set -uo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
# node v26 first so the bnbagent SDK (Node >= 22) resolves; then src on the path.
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src"
# Non-secret gates the steady .env lacks (mirrors `make commerce_serve`). AGENT_WALLET_PASSWORD +
# ERC8183_STORAGE come from .env. Provider side only — no buyer keystore is needed here.
export ERC8183_ENABLED=true
export ERC8183_NETWORK="${ERC8183_NETWORK:-bsc-mainnet}"
# Best-effort IPFS pinning key from .env (only when storage=ipfs); absence degrades to local storage.
if [ -z "${STORAGE_API_KEY:-}" ]; then
  _jwt="$(grep -E '^JWT_SECRET=' .env 2>/dev/null | cut -d= -f2-)"
  [ -n "$_jwt" ] && export STORAGE_API_KEY="$_jwt"
fi
mkdir -p data/logs

INTERVAL_S="${COMMERCE_INTERVAL_S:-60}"
MAX_CYCLES="${COMMERCE_MAX_CYCLES:-0}"
# Bound each cycle so a slow/hung mainnet RPC (the get_pending_jobs read) can't wedge the daemon.
CYCLE_TIMEOUT_S="${COMMERCE_CYCLE_TIMEOUT_S:-120}"
TIMEOUT_CMD=""
if command -v timeout >/dev/null 2>&1; then TIMEOUT_CMD="timeout ${CYCLE_TIMEOUT_S}s"
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT_CMD="gtimeout ${CYCLE_TIMEOUT_S}s"; fi
LOG="data/logs/commerce_serve.log"
HEARTBEAT="data/logs/commerce_serve_heartbeat.ts"
KILL="data/KILL_SWITCH_ENGAGED"
JRNL="data/journal/commerce_jobs.jsonl"
PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY="python3"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$1" | tee -a "$LOG"; }
jrnl_sig() { [ -f "$JRNL" ] && stat -f '%m:%z' "$JRNL" 2>/dev/null || echo "0:0"; }

# Single-instance guard for the whole daemon lifetime (not per-cycle).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.commerce_serve.flock"
  if ! flock -n 9; then
    log "=== $(ts) SKIP: a commerce_serve daemon is already running ==="
    exit 0
  fi
fi

trap 'log "=== $(ts) commerce_serve STOP (signal) after $cycle cycles ==="; exit 0' INT TERM

log "=== $(ts) commerce_serve START (interval=${INTERVAL_S}s network=${ERC8183_NETWORK} max_cycles=${MAX_CYCLES}) ==="
cycle=0
while true; do
  cycle=$((cycle + 1))
  if [ -f "$KILL" ]; then
    log "  cycle $cycle  $(ts)  KILL SWITCH engaged ($KILL) — skipping poll (no submit)"
  else
    sig_before="$(jrnl_sig)"
    set +e
    $TIMEOUT_CMD "$PY" scripts/erc8183_serve.py --once >> "$LOG" 2>&1
    rc=$?
    set -e 2>/dev/null || true
    # rc=2 on the FIRST cycle means a hard misconfig (SDK missing / ERC8183 off / no provider key):
    # exit loudly instead of spinning forever. A later transient rc=2 just logs and continues.
    if [ "$rc" = "2" ] && [ "$cycle" = "1" ]; then
      log "  cycle $cycle  $(ts)  rc=2 commerce unavailable — need ERC8183_ENABLED + bnbagent SDK + AGENT_WALLET_PASSWORD. Stopping."
      exit 2
    fi
    # rc=124 = the cycle hit CYCLE_TIMEOUT_S (slow/hung RPC) — benign; the next fresh cycle retries.
    [ "$rc" = "124" ] && log "  cycle $cycle  $(ts)  rc=124 cycle timed out (${CYCLE_TIMEOUT_S}s) — slow RPC, retrying next cycle"
    log "  cycle $cycle  $(ts)  rc=$rc"
    # Push the fresh snapshot ONLY when the commerce ledger actually changed (a job was served).
    if [ "$(jrnl_sig)" != "$sig_before" ]; then
      log "  cycle $cycle  $(ts)  commerce ledger changed — publishing snapshot"
      bash "$REPO/scripts/publish_snapshot.sh" >> "$LOG" 2>&1 || true
    fi
  fi
  echo "$(date +%s)000" > "$HEARTBEAT"
  if [ "$MAX_CYCLES" -gt 0 ] && [ "$cycle" -ge "$MAX_CYCLES" ]; then
    log "=== $(ts) commerce_serve DONE ($cycle/$MAX_CYCLES cycles) ==="
    break
  fi
  sleep "$INTERVAL_S"
done
