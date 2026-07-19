"""
Live dashboard ingest — the ONE write endpoint on the otherwise read-only deploy.

After each rebalance the operator's local tick regenerates the live snapshot and POSTs it here;
`/api/snapshot` then serves it (while fresh) instead of the baked seed, so the public dashboard
reflects current data within one poll (~4s) — no git push / image rebuild. See scripts/publish_snapshot.sh.

SAFETY CONTRACT (this is a WRITE on a public deploy, so it is locked down):
  - DEFAULT-DENY: if `INGEST_TOKEN` is unset on the server, the endpoint 503s — an unconfigured
    deploy can't be written to at all.
  - AUTH: a constant-time (`hmac.compare_digest`) check of the `X-Ingest-Token` header → 401 on miss.
  - The payload is PUBLIC market data (NAV, weights, rationale, regime) — never a key/password. The
    worst case of a leaked token is dashboard SPOOFING; no funds/keys are reachable from here.
  - It writes ONE fixed file (reads.PUSHED) via an atomic helper — no path comes from the request,
    so there is no traversal/overwrite surface. Body is size-capped to reject abuse.
"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ictbot.api import reads
from ictbot.settings import settings

router = APIRouter(prefix="/api/ingest")

_MAX_BODY_BYTES = 2_000_000  # a snapshot is ~50-200 KB; 2 MB is generous + caps abuse


@router.post("/snapshot")
async def ingest_snapshot(request: Request):
    """Accept a freshly-generated live snapshot and store it as the served override (token-gated)."""
    token = settings.ingest_token
    if not token:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "message": "ingest disabled (no INGEST_TOKEN configured)"},
        )

    provided = request.headers.get("x-ingest-token", "")
    if not hmac.compare_digest(provided, token):
        return JSONResponse(
            status_code=401, content={"ok": False, "message": "bad or missing X-Ingest-Token"}
        )

    # Size guard: reject early on Content-Length, then again after reading (header can lie/be absent).
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > _MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"ok": False, "message": "snapshot too large"})
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"ok": False, "message": "snapshot too large"})

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return JSONResponse(
            status_code=400, content={"ok": False, "message": "body is not valid JSON"}
        )
    # Minimal shape check — a real snapshot carries these; rejects garbage that would blank the dashboard.
    if not isinstance(data, dict) or "served_at" not in data or "nav" not in data:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "not a snapshot (missing served_at/nav)"},
        )

    try:
        reads.store_pushed_snapshot(data)
        reads.invalidate_snapshot_cache()  # next /api/snapshot rebuild reflects this push immediately
    except Exception as e:  # never leak a stack trace; surface a short message
        return JSONResponse(
            status_code=500, content={"ok": False, "message": f"store failed: {str(e)[:120]}"}
        )
    return {"ok": True, "served_at": data.get("served_at")}
