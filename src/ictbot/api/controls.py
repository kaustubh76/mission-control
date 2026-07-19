"""
The two guarded demo controls: run ONE sim rebalance tick, and engage/release the
kill switch. Kept separate from the read surface so the (small) write path is
trivially auditable.

SAFETY CONTRACT:
  - sim-tick passes mode="sim" as a HARDCODED LITERAL. No request field and no
    setting (not even twak_mode="live") can make it touch the chain — tick("sim")
    builds a sim TwakSpotBroker(live=False). It also refuses (409) while the kill
    switch is engaged.
  - kill engages/releases the same sentinel the scanner/agent check; releasing does
    NOT enable live trading (that still needs a manual .env edit).
"""

from __future__ import annotations

import asyncio
import importlib.util

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ictbot.api.schemas import (
    KillIn,
    KillOut,
    SimTickOut,
    StrategySelectIn,
    StrategySelectOut,
    TokensIn,
    TokensOut,
)
from ictbot.runtime import kill_switch
from ictbot.settings import PROJECT_ROOT, settings

router = APIRouter(prefix="/api/controls")

_tick_lock = asyncio.Lock()
_run_allocator = None  # lazily loaded so app boot stays light


def _load_tick():
    """Import scripts/run_allocator.py once (it isn't a package) and return its
    `tick` fn — reuses the exact production tick, no logic duplication."""
    global _run_allocator
    if _run_allocator is None:
        path = PROJECT_ROOT / "scripts" / "run_allocator.py"
        spec = importlib.util.spec_from_file_location("run_allocator", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load run_allocator from {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _run_allocator = mod
    return _run_allocator.tick


@router.post("/sim-tick")
async def sim_tick():
    if kill_switch.is_engaged():
        return JSONResponse(
            status_code=409,
            content={"ok": False, "rc": None, "message": "kill switch engaged — refusing to trade"},
        )
    if _tick_lock.locked():
        return JSONResponse(
            status_code=409,
            content={"ok": False, "rc": None, "message": "a sim tick is already running"},
        )
    async with _tick_lock:
        try:
            tick = _load_tick()
            # mode is a literal "sim" — never derived from the request or settings.
            rc = await asyncio.to_thread(tick, "sim", settings.max_drawdown_frac)
        except Exception as e:  # surfaced to the UI, never crashes the server
            return SimTickOut(ok=False, rc=None, message=f"tick failed: {e}")
    msg = {0: "rebalanced (sim)", 1: "drawdown halt triggered", 2: "insufficient candle data"}
    return SimTickOut(ok=rc in (0, 2), rc=rc, message=msg.get(rc, "done"))


@router.post("/kill", response_model=KillOut)
async def kill(body: KillIn):
    if body.engage:
        kill_switch.engage(reason=body.reason or "dashboard")
        return KillOut(
            ok=True, engaged=True, message="kill switch ENGAGED — agent refuses new trades"
        )
    kill_switch.release()
    return KillOut(
        ok=True,
        engaged=False,
        message="kill switch released (live trading still requires a manual .env change)",
    )


@router.post("/tokens", response_model=TokensOut)
async def set_tokens(body: TokensIn):
    """Set the active token universe the allocator ranks over. Config, not a
    trade — deliberately NOT blocked by the kill switch (the tick itself is
    already gated). Applies at the start of the NEXT tick; a deselected token
    that's still held gets target 0 then and is sold by that rebalance."""
    from ictbot.runtime import active_tokens

    try:
        saved = active_tokens.save(body.active)
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "active": active_tokens.load(), "message": str(e)},
        )
    return TokensOut(
        ok=True,
        active=saved,
        message=(
            f"{len(saved)}/{len(active_tokens.universe())} tokens active — "
            "deselected holdings sell on the next rebalance tick"
        ),
    )


@router.post("/strategy", response_model=StrategySelectOut)
async def set_strategy(body: StrategySelectIn):
    """Set the registered strategy the allocator runs. Config, not a trade — not
    kill-gated. SIM-ONLY: the runtime reads this file only in sim mode, so this can
    never change the LIVE/contest strategy (server-enforced in run_allocator). Applies
    at the start of the next SIM tick."""
    from ictbot.runtime import strategy_select
    from ictbot.strategy import registry

    try:
        saved = strategy_select.save(body.strategy)
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "strategy": strategy_select.load("momentum_adaptive"),
                "available": registry.available(),
                "message": str(e),
            },
        )
    return StrategySelectOut(
        ok=True,
        strategy=saved,
        available=registry.available(),
        message=f"SIM strategy set to '{saved}' — applies next sim tick (live is unaffected)",
    )
