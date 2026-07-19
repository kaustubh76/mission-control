"""
Phase D — TG operator pause.

A file-based, time-bounded pause that the scanner main loop checks each
tick. Companion to `kill_switch.py` but auto-expiring: the operator can
type `/pause 30` in Telegram to halt evaluation for 30 minutes without
having to remember to send `/resume`. After the timestamp passes, the
sentinel is auto-cleared on the next `is_active()` call so the bot
resumes evaluating without manual intervention.

API mirrors `kill_switch.py`:
    pause.engage(seconds=600)   # halt for 10 minutes
    pause.release()             # clear immediately
    pause.is_active()           # cheap per-iteration probe

Persistence shape: `data/PAUSED_UNTIL` contains a single ISO-8601 UTC
timestamp. Survives restarts so a process recycle doesn't unpause early.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

PAUSED_UNTIL_FILE = Path("data") / "PAUSED_UNTIL"


def engage(seconds: int) -> datetime:
    """Pause until `now + seconds`. Atomic write.

    Returns the resolved expiry timestamp so callers can echo it back
    to the operator ("paused until 13:42 UTC").
    """
    seconds = max(0, int(seconds))
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    PAUSED_UNTIL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PAUSED_UNTIL_FILE.with_suffix(".tmp")
    tmp.write_text(until.isoformat(), encoding="utf-8")
    os.replace(tmp, PAUSED_UNTIL_FILE)
    return until


def release() -> None:
    """Clear the pause immediately. No-op if not paused."""
    try:
        PAUSED_UNTIL_FILE.unlink()
    except FileNotFoundError:
        pass


def is_active() -> bool:
    """True only while `now < PAUSED_UNTIL`.

    Stale files (timestamp in the past) are auto-deleted so the scanner
    can resume without operator action.
    """
    if not PAUSED_UNTIL_FILE.exists():
        return False
    try:
        raw = PAUSED_UNTIL_FILE.read_text(encoding="utf-8").strip()
        until = datetime.fromisoformat(raw)
    except (OSError, ValueError):
        # Corrupt file — treat as inactive and clear so the operator
        # doesn't have to delete it by hand.
        release()
        return False
    if datetime.now(timezone.utc) >= until:
        release()
        return False
    return True


def remaining_seconds() -> int:
    """Seconds until the pause expires; 0 when not active."""
    if not PAUSED_UNTIL_FILE.exists():
        return 0
    try:
        until = datetime.fromisoformat(PAUSED_UNTIL_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    delta = (until - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(delta))
