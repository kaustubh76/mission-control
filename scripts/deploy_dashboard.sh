#!/usr/bin/env bash
# One-command dashboard redeploy (e.g. after the ERC-8183 "U" faucet + a real job settles).
#
#   make deploy_dashboard
#
# Steps: regenerate web/public/snapshot.json from the LIVE state (includes pillars.commerce from
# data/journal/commerce_jobs.jsonl) -> reseed infra/seed (for the Render API image) -> build the
# Vite SPA -> deploy the PREBUILT Build Output to the canonical Vercel project.
#
# Why prebuilt: the Vercel project's framework preset rejects a plain `vercel --prod` build
# ("No Next.js version detected"). Shipping `.vercel/output` (Build Output API v3) bypasses the
# remote framework build entirely and uploads the static SPA we already validated locally.
#
# To surface the LIVE commerce numbers, set ERC8183_ENABLED=true (so the snapshot's commerce block
# reads `enabled`) before running — the figures themselves come from commerce_jobs.jsonl regardless.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PROJECT="${VERCEL_PROJECT:-bnb-mission-control}"   # the canonical project (bnb-mission-control-two.vercel.app)
CANON_URL="https://bnb-mission-control-two.vercel.app"
# Render API origin the SPA's same-origin /api/* is edge-proxied to (see SCALABILITY.md §4). Keeping
# the SPA same-origin lets Vercel's edge cache /api/snapshot per its s-maxage so N viewers collapse
# into ~1 origin hit per window (and it kills CORS). Override for a different API host.
API_ORIGIN="${DASHBOARD_API_ORIGIN:-https://bnb-mission-control-api.onrender.com}"

# node/npm/vercel live under nvm and are NOT on a non-login `make` shell's PATH — source nvm if missing.
if ! command -v npm >/dev/null 2>&1 || ! command -v vercel >/dev/null 2>&1; then
  # shellcheck disable=SC1090
  [ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 || true
fi
command -v vercel >/dev/null 2>&1 || { echo "ERROR: vercel CLI not found (npm i -g vercel, or check nvm)."; exit 1; }

echo "==> 1/5 regenerate snapshot (live pillars incl. ERC-8183 commerce)"
PYTHONPATH=src .venv/bin/python scripts/export_snapshot.py

echo "==> 2/5 reseed infra/seed (so a Render redeploy on git push serves the same data)"
for f in commerce_jobs.jsonl allocator_journal.jsonl allocator_state.json; do
  [ -f "data/journal/$f" ] && cp "data/journal/$f" infra/seed/ && echo "    + infra/seed/$f" || true
done

echo "==> 3/5 build the dashboard (typecheck + bundle)"
( cd web && npm run build )

echo "==> 4/5 stage the prebuilt Build Output (bypasses the framework preset)"
cd web
rm -rf .vercel/output && mkdir -p .vercel/output/static
cp -R dist/. .vercel/output/static/
# Build Output API v3 routes (prebuilt mode IGNORES web/vercel.json — these routes are authoritative):
#   1. /api/* is reverse-proxied to the Render API so the SPA is SAME-ORIGIN. Vercel's edge then
#      honors the API's Cache-Control s-maxage on /api/snapshot → N viewers collapse to ~1 origin
#      hit/window, and there is no CORS. /api/agent-hub/ping and POSTs carry no cache header upstream
#      so they always reach origin. If Vercel ever stops caching the external proxy, front Render
#      with Cloudflare instead (see SCALABILITY.md §4) — no app change needed.
#   2. filesystem serves the static SPA assets; 3. SPA fallback to index.html for client routes.
cat > .vercel/output/config.json <<EOF
{"version":3,"routes":[{"src":"/api/(.*)","dest":"$API_ORIGIN/api/\$1"},{"handle":"filesystem"},{"src":"/(.*)","dest":"/index.html"}]}
EOF
vercel link --project "$PROJECT" --yes >/dev/null 2>&1   # ensure the canonical project, not a stray local link

echo "==> 5/5 deploy prebuilt to production ($PROJECT)"
vercel deploy --prebuilt --prod --yes

echo "==> verify $CANON_URL/snapshot.json"
curl -sL --max-time 25 "$CANON_URL/snapshot.json" \
  | python3 -c "import sys,json; c=json.load(sys.stdin)['pillars'].get('commerce') or {}; print(f\"    commerce: enabled={c.get('enabled')} served={c.get('jobs_served')} revenue_u={c.get('revenue_u')}\")" \
  || echo "    (could not read deployed snapshot — check the deploy URL above)"

echo "done. NOTE: 'git push' redeploys the Render API (serves the reseeded infra/seed data)."
