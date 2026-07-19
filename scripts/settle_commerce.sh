#!/usr/bin/env bash
# Finalize served-but-unsettled ERC-8183 commerce jobs whose OPTIMISTIC dispute window has closed.
#
# The agent SELLS its Market Regime Report (ERC-8183); a served job is delivered on-chain immediately but
# sits SETTLE_DEFERRED until its ~7-day dispute window elapses (`settle()` reverts NotDecided,
# selector 0x17be5b7b, until then — EXPECTED, not a failure). This wrapper calls the buyer-side
# finalizer once per run; it's idempotent (still-deferred jobs stay deferred, already-settled jobs are
# skipped), so a SETTLE row lands on-chain the first day each window opens. See
# docs/erc8183_agent_commerce.md.
#
# Cron it for the back half of the contest week (jobs 25741/26506 submitted 06-17 → settle ≈06-24):
#   0 14 24-28 6 *  "/Users/apple/Desktop/BNB-Hack-CMC/scripts/settle_commerce.sh"
# Watch:  tail -f data/logs/settle_commerce.log
#
# SECRETS: the buyer keystore creds (CLIENT_WALLET_PASSWORD / CLIENT_WALLET_DIR / ...) are read from
# settings (i.e. the gitignored .env) — NEVER hardcoded here. If you keep buyer creds out of .env, put
# them in $REPO/.env.commerce.local (matches the .env*.local gitignore rule); it is sourced if present.
# With no buyer creds, commerce.buyer_available() is False → the finalizer no-ops cleanly (logged).
set -uo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
# node v26 first so `twak`/`node` (and the SDK's gas path) resolve correctly.
export PATH="/Users/apple/.nvm/versions/node/v26.3.0/bin:$PATH"
export PYTHONPATH="$REPO/src"          # shadow the stale editable .pth (loads the wrong tree)
export ERC8183_ENABLED=true            # non-secret: enable the commerce surface for this run
export ERC8183_NETWORK=bsc-mainnet     # the real jobs are on mainnet — settle there, not testnet
# Optional: buyer creds kept separate from .env (gitignored *.local). Settings also auto-loads .env.
[ -f "$REPO/.env.commerce.local" ] && set -a && . "$REPO/.env.commerce.local" && set +a
mkdir -p data/logs

# Overlap guard (flock where available; degrades gracefully on stock macOS).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REPO/data/.settle_commerce.flock"
  if ! flock -n 9; then
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) SKIP: prior settle still running ===" >> data/logs/settle_commerce.log
    exit 0
  fi
fi

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) settle_commerce ===" >> data/logs/settle_commerce.log
.venv/bin/python scripts/erc8183_create_job.py --settle-pending >> data/logs/settle_commerce.log 2>&1
rc=$?
echo "--- $(date -u +%Y-%m-%dT%H:%M:%SZ) finalizer rc=$rc (0=ok/deferred, 1=settle error, 2=buyer creds absent) ---" >> data/logs/settle_commerce.log
exit 0   # cron-safe: best-effort; the log carries the detail, never crash the scheduler
