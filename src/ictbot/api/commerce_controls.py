"""
One guarded commerce control: create + serve a REAL ERC-8183 job end-to-end so the agent's
on-chain job ledger fills with a genuine served job (the "buy side" the dashboard was missing).

SAFETY CONTRACT (mirrors controls.py):
  - OPERATOR-LOCAL ONLY. The full loop signs on BOTH sides (provider identity keystore + a DISTINCT
    buyer keystore), so it runs only where both passwords are set. The read-only cloud deploy has
    neither → `commerce.buyer_available()` is False → this returns 403 and never attempts to sign.
  - Real on-chain round-trips are slow + must not overlap, so a single in-flight job is allowed
    (409 while busy). The work runs off the event loop via `asyncio.to_thread`.
  - The response is PUBLIC job metadata only (ids, hashes, amounts, public addresses) — never a
    secret. The deliverable itself is public CMC market analysis (see commerce.on_job).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ictbot.agent import commerce
from ictbot.api.schemas import (
    CommerceCreateJobIn,
    CommerceCreateJobOut,
    CommercePreviewIn,
    CommercePreviewReportOut,
    CommerceSettleIn,
    CommerceSettleOut,
)

router = APIRouter(prefix="/api/commerce")

_job_lock = asyncio.Lock()


@router.post("/create-job", response_model=CommerceCreateJobOut)
async def create_job(body: CommerceCreateJobIn):
    if not commerce.buyer_available():
        # Operator-only: no signing key in this process (the cloud deploy is intentionally inert).
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "message": "operator-only: ERC-8183 job creation needs ERC8183_ENABLED, the SDK, "
                "and BOTH AGENT_WALLET_PASSWORD + CLIENT_WALLET_PASSWORD (a distinct buyer keystore) "
                "set locally — unavailable on the read-only deploy.",
            },
        )
    if _job_lock.locked():
        return JSONResponse(
            status_code=409,
            content={"ok": False, "message": "a commerce job is already in flight — try again shortly"},
        )
    async with _job_lock:
        try:
            result = await asyncio.to_thread(
                commerce.create_and_serve_job,
                body.description,
                amount=body.amount,
                expiry_min=body.expiry_min,
            )
        except Exception as e:  # surfaced to the UI, never crashes the server
            return CommerceCreateJobOut(ok=False, message=f"create-job failed: {e}")
    return CommerceCreateJobOut(**result)


@router.post("/settle", response_model=CommerceSettleOut)
async def settle(body: CommerceSettleIn):
    """FINALIZE served-but-unsettled jobs once their optimistic dispute window has closed. Same
    operator-local contract as create-job: 403 on the read-only deploy, the SAME single-job lock
    (buyer signing must not overlap), off the event loop, and never a 500. A job still inside its
    window comes back as `deferred` (not an error) — expected before the ~7-day window elapses."""
    if not commerce.buyer_available():
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "message": "operator-only: settling needs ERC8183_ENABLED, the SDK, and BOTH "
                "AGENT_WALLET_PASSWORD + CLIENT_WALLET_PASSWORD set locally — unavailable on the "
                "read-only deploy.",
            },
        )
    if _job_lock.locked():
        return JSONResponse(
            status_code=409,
            content={"ok": False, "message": "a commerce job is already in flight — try again shortly"},
        )
    async with _job_lock:
        try:
            result = await asyncio.to_thread(commerce.settle_pending_jobs, body.job_ids)
        except Exception as e:  # surfaced to the UI, never crashes the server
            return CommerceSettleOut(ok=False, message=f"settle failed: {e}")
    return CommerceSettleOut(**result)


@router.post("/preview", response_model=CommercePreviewReportOut)
async def preview(body: CommercePreviewIn):
    """READ-ONLY: build the live Market Regime Report the agent SELLS — no wallet, no chain action, no
    signing. UNGUARDED on purpose: it spends nothing, so it works EVERYWHERE, including the
    zero-secret cloud deploy (where create-job/settle 403). This is the genuine ERC-8183 deliverable
    a buyer would receive, minus the on-chain escrow — so a visitor can click and see real CMC
    intelligence. Runs off the event loop; never a 500 (build_report itself never raises)."""
    from ictbot.agent.regime_report import build_report

    try:
        rep = await asyncio.to_thread(build_report, query=(body.description or None))
    except Exception as e:  # defensive — build_report is contracted never-raise
        return CommercePreviewReportOut(ok=False, message=f"preview failed: {e}")
    status = rep.get("status")
    return CommercePreviewReportOut(
        ok=True,
        status=status,
        issuer=rep.get("issuer"),
        strategy=rep.get("strategy"),
        regime_score=rep.get("regime_score"),
        deploy_cap=rep.get("deploy_cap"),
        ta_health=rep.get("ta_health"),
        fear_greed=rep.get("fear_greed"),
        momentum_ranking=rep.get("momentum_ranking") or [],
        target_weights=rep.get("target_weights") or {},
        rationale=rep.get("rationale") or "",
        cmc_sources=rep.get("cmc_sources"),
        market_overview=rep.get("market_overview"),
        ts=rep.get("ts"),
        message=("" if status == "ok" else (rep.get("reason") or "degraded")),
    )
