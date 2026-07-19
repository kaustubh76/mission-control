#!/usr/bin/env bash
# Auto-selector DEMO + smoke test — drives a real STAY → SWITCH in a throwaway isolated data dir
# (the .py sets its own ALLOCATOR_DATA_DIR; the live data/ tree and .env are never touched).
# Exits non-zero if the engine fails to fire the switch, so it's safe to wire into CI.
#
# Run:   bash scripts/demo_auto_selector.sh
set -euo pipefail

REPO="/Users/apple/Desktop/BNB-Hack-CMC"
cd "$REPO" || exit 1
export PYTHONPATH="$REPO/src:$REPO"     # src shadows stale .pth; repo root makes `scripts.*` importable

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" scripts/demo_auto_selector.py
