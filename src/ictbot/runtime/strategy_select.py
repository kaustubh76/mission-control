"""
UI-controlled strategy selector — which registered PortfolioStrategy the allocator
runs on the SIM track.

A *file*-based control (mirrors `active_tokens.py` / `kill_switch.py`): the dashboard
POSTs the desired strategy name, we persist it atomically, and `run_allocator._tick()`
re-reads it fresh at the top of every tick. No restart needed.

CONTEST SAFETY: this is read by the runtime **only in SIM mode**. The LIVE/contest
track ignores this file entirely and runs `settings.strategy_name` / the locked
`momentum_adaptive` default — so a dashboard click can never alter the live strategy.
Enforced at the dispatch in run_allocator, not just here.

The file is advisory config, not a safety control: absent, corrupt, or no-longer-valid
content (e.g. a name not in the registry) degrades to the supplied default, never to a
halt. Registry names are lowercase snake_case — we match case-insensitively but always
return the CANONICAL registry name.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ictbot.settings import JOURNAL_DIR

STRATEGY_SELECT_FILE = JOURNAL_DIR / "strategy_select.json"


def available() -> list[str]:
    """Registered strategy names. Lazy import so this module stays light."""
    from ictbot.strategy import registry

    return registry.available()


def _canonical(name: str, uni: list[str]) -> str | None:
    """Map a (possibly mis-cased) name to its canonical registry name, or None."""
    want = str(name).strip().lower()
    for s in uni:
        if s.lower() == want:
            return s
    return None


def load(default: str) -> str:
    """Current selected strategy (canonical registry name), else `default`.

    Degrades to `default` on any problem: file absent, unreadable, corrupt JSON, or a
    persisted name that is not (or no longer) a registered strategy.
    """
    try:
        raw = json.loads(STRATEGY_SELECT_FILE.read_text(encoding="utf-8"))
        wanted = raw.get("strategy", "")
    except Exception:
        return default
    return _canonical(wanted, available()) or default


def save(strategy: str) -> str:
    """Validate + atomically persist the chosen strategy name.

    Returns the canonical registry name actually saved. Raises ValueError on an
    unknown strategy — the API maps that to a 400 with the message verbatim.
    """
    uni = available()
    canonical = _canonical(strategy, uni)
    if canonical is None:
        raise ValueError(f"unknown strategy '{strategy}' — registered: {uni}")
    payload = {
        "strategy": canonical,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "dashboard",
    }
    STRATEGY_SELECT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STRATEGY_SELECT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STRATEGY_SELECT_FILE)
    return canonical
