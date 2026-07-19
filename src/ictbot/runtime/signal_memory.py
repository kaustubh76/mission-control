"""
Persists the last signal that was sent to Telegram so we don't spam the
same BUY/SELL repeatedly while the conditions remain true.

Also persists per-pair last near-miss state with the same atomic-write
pattern so the TG near-miss alert doesn't refire while the same
blocker remains in place.
"""

import json
import os

from ictbot.settings import NEAR_MISS_FILE, SIGNAL_FILE


def load_last_signal() -> dict:
    if not SIGNAL_FILE.exists():
        return {}
    try:
        with open(SIGNAL_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_last_signal(data: dict) -> None:
    """J9 (audit gap #17): atomic write so a reader (dashboard, supervisor)
    never observes a half-flushed file. Same temp-file + os.replace
    pattern as the journal."""
    tmp = SIGNAL_FILE.with_suffix(SIGNAL_FILE.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, SIGNAL_FILE)


def load_last_near_miss() -> dict:
    """Return a {pair: state_key} mapping of the last near-miss TG'd.
    state_key encodes pair + would-be direction + primary blocker so the
    alert refires when the bot edges closer (blocker changes) but stays
    silent when nothing has materially moved.
    """
    if not NEAR_MISS_FILE.exists():
        return {}
    try:
        with open(NEAR_MISS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_last_near_miss(data: dict) -> None:
    tmp = NEAR_MISS_FILE.with_suffix(NEAR_MISS_FILE.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, NEAR_MISS_FILE)
