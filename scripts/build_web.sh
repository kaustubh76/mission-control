#!/usr/bin/env bash
#
# Build the React "Mission Control" SPA locally, working around the repo path.
#
# WHY THIS EXISTS: this repo lives at ".../BNB Hack * CMC". The "*" is glob-expanded
# by esbuild when Vite bundles its own config, which kills `vite build`/`vite dev`
# run in-place ("Must use outdir when there are multiple input files"). esbuild
# resolves the realpath, so symlinks don't help. The fix is to build in a clean
# staging dir and copy the bundle back into web/dist.
#
# Production is UNAFFECTED — the Dockerfile builds at /app (no special chars), so
# this script is a LOCAL convenience only. CI/Docker call `npm run build` directly.
#
# Usage:
#   scripts/build_web.sh            # stage -> build -> copy dist back to web/dist
#   WEB_STAGE=/path scripts/build_web.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_WEB="$REPO_ROOT/web"
STAGE="${WEB_STAGE:-/tmp/bnb-web-build}"

if [ ! -d "$REPO_WEB" ]; then
  echo "no web/ dir at $REPO_WEB" >&2
  exit 1
fi

mkdir -p "$STAGE"

# Refresh the static data snapshot (the SPA's offline fallback) before building,
# so public/snapshot.json carries the latest real allocator state.
PYBIN="$REPO_ROOT/.venv/bin/python"
[ -x "$PYBIN" ] || PYBIN="python3"
"$PYBIN" "$REPO_ROOT/scripts/export_snapshot.py" || echo "snapshot export skipped"

# Sync sources into the clean staging path (never node_modules/dist — those are
# large and platform-built; node_modules is copied once below).
rsync -a --delete --exclude node_modules --exclude dist "$REPO_WEB"/ "$STAGE"/

# Reuse node_modules across builds: copy from the repo install once (no network),
# fall back to `npm ci` if the repo hasn't been installed yet.
if [ ! -d "$STAGE/node_modules" ]; then
  if [ -d "$REPO_WEB/node_modules" ]; then
    cp -R "$REPO_WEB/node_modules" "$STAGE/node_modules"
  else
    (cd "$STAGE" && npm ci --no-audit --no-fund)
  fi
fi

(cd "$STAGE" && npm run build)

# Copy the fresh bundle back into the repo so FastAPI StaticFiles can serve it.
rm -rf "$REPO_WEB/dist"
cp -R "$STAGE/dist" "$REPO_WEB/dist"
echo "built SPA -> $REPO_WEB/dist"
