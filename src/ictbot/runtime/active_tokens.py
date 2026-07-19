"""
UI-controlled token universe — which subset of CONTEST_TOKENS the allocator
may rank and buy.

A *file*-based control (mirrors `kill_switch.py`): the dashboard's token
toggles POST the desired active list, we persist it atomically, and
`run_allocator._tick()` re-reads the file fresh at the top of every tick.
No process restart needed; a mid-tick change simply applies next tick.

Semantics (deliberate):
  - RANKING universe   = active tokens (top-k picked only from these).
  - BROKER universe    = active ∪ still-held tokens, so a deselected token
    with a balance gets target weight 0 and is sold on the NEXT rebalance —
    never silently stranded outside the trading loop.
  - Regime/breadth stays full-universe (it's a market gauge, not a portfolio
    gauge) — that's handled at the call sites, not here.

The file is advisory config, not a safety control: absent, corrupt, or
no-longer-valid content (e.g. ALLOC_TOP_K raised above the saved count)
degrades to the full contest universe, never to a halt.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ictbot.settings import JOURNAL_DIR, settings

ACTIVE_TOKENS_FILE = JOURNAL_DIR / "active_tokens.json"


def universe() -> tuple[str, ...]:
    """The full contest universe, canonical order. Lazy import (matches
    reads.py) so importing this module stays light."""
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    return CONTEST_TOKENS


def min_required() -> int:
    """The smallest legal active set: top-k needs candidates, floor of 2."""
    return max(2, settings.alloc_top_k)


def load() -> list[str]:
    """Current active token list, in canonical CONTEST_TOKENS order.

    Degrades to the FULL universe on any problem: file absent, unreadable,
    corrupt JSON, unknown tokens only, or a persisted set that has since
    become too small (e.g. ALLOC_TOP_K was raised in .env after the save).
    """
    uni = universe()
    try:
        raw = json.loads(ACTIVE_TOKENS_FILE.read_text(encoding="utf-8"))
        wanted = {str(t).upper() for t in raw.get("active", [])}
    except Exception:
        return list(uni)
    active = [t for t in uni if t in wanted]
    if len(active) < min_required():
        return list(uni)
    return active


def save(active: list[str]) -> list[str]:
    """Validate + atomically persist the desired active list.

    Returns the canonical-order list actually saved. Raises ValueError on
    unknown tokens or a set smaller than `min_required()` — the API maps
    that to a 400 with the message verbatim.
    """
    uni = universe()
    wanted = {str(t).upper() for t in active}
    unknown = sorted(wanted - set(uni))
    if unknown:
        raise ValueError(f"unknown token(s): {unknown} — universe is {list(uni)}")
    canonical = [t for t in uni if t in wanted]
    need = min_required()
    if len(canonical) < need:
        raise ValueError(
            f"need at least {need} active tokens (top_k={settings.alloc_top_k}), got {len(canonical)}"
        )
    payload = {
        "active": canonical,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "dashboard",
    }
    ACTIVE_TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACTIVE_TOKENS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, ACTIVE_TOKENS_FILE)
    return canonical
