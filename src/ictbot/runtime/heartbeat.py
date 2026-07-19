"""
J10 (audit gap #18) — scanner heartbeat.

The scanner's `while True:` loop has no liveness signal. A hung process
under a network-partition deadlock looks identical to a healthy one
from the outside. This module writes `data/logs/heartbeat.ts` once per
scan tick so a supervisor (cron, systemd timer, monit) can alert on
staleness.

Used by:
  - `ictbot.orchestrator.scanner.main` (writer)
  - external supervision scripts (reader)

Atomic write: write to .tmp then `os.replace` so a partial flush never
leaves a half-written file that a reader would misinterpret.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ictbot.settings import LOGS_DIR

HEARTBEAT_FILE = LOGS_DIR / "heartbeat.ts"


def beat(now: float | None = None) -> None:
    """Stamp the heartbeat file with the current epoch seconds."""
    ts = now if now is not None else time.time()
    payload = f"{ts:.3f}\n"
    tmp = HEARTBEAT_FILE.with_suffix(".ts.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, HEARTBEAT_FILE)


def last_beat() -> float | None:
    """Read the most recent heartbeat timestamp, or None if absent/corrupt."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        return float(HEARTBEAT_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def age_seconds() -> float | None:
    """Seconds since the last heartbeat. None if no heartbeat exists."""
    last = last_beat()
    if last is None:
        return None
    return max(0.0, time.time() - last)


def is_stale(max_age_seconds: float) -> bool:
    """True when the heartbeat is older than `max_age_seconds`, OR missing."""
    age = age_seconds()
    if age is None:
        return True
    return age > max_age_seconds


def _set_path_for_tests(path: Path) -> None:
    """Test hook — point at a temp file so we don't clobber real logs."""
    global HEARTBEAT_FILE
    HEARTBEAT_FILE = path
