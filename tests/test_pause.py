"""
Phase D — TG operator pause module tests.

Verifies the file-based pause sentinel: engage writes, is_active reads,
release deletes, stale files auto-clear.
"""

from __future__ import annotations

import pytest

from ictbot.runtime import pause


@pytest.fixture(autouse=True)
def isolate_pause_file(tmp_path, monkeypatch):
    """Each test gets its own PAUSED_UNTIL path so they don't interfere."""
    monkeypatch.setattr(pause, "PAUSED_UNTIL_FILE", tmp_path / "PAUSED_UNTIL")
    yield


def test_engage_writes_file_and_is_active_returns_true():
    assert pause.is_active() is False
    until = pause.engage(seconds=60)
    assert pause.PAUSED_UNTIL_FILE.exists()
    assert pause.is_active() is True
    # remaining_seconds is bounded by the requested window
    assert 0 < pause.remaining_seconds() <= 60
    assert until is not None


def test_release_clears_pause():
    pause.engage(seconds=60)
    assert pause.is_active() is True
    pause.release()
    assert pause.is_active() is False
    assert not pause.PAUSED_UNTIL_FILE.exists()


def test_release_is_noop_when_not_paused():
    """release() on a clean state must not raise."""
    pause.release()  # no exception
    assert pause.is_active() is False


def test_stale_file_auto_clears_on_probe():
    """If PAUSED_UNTIL is in the past, is_active() must delete it
    AND return False so the bot resumes without operator action."""
    pause.engage(seconds=0)
    # Backdate the sentinel by writing a past timestamp directly.
    from datetime import datetime, timedelta, timezone

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    pause.PAUSED_UNTIL_FILE.write_text(past)
    assert pause.is_active() is False
    assert not pause.PAUSED_UNTIL_FILE.exists()


def test_corrupt_file_treated_as_inactive():
    """Garbage in PAUSED_UNTIL → is_active=False and file is removed."""
    pause.PAUSED_UNTIL_FILE.parent.mkdir(parents=True, exist_ok=True)
    pause.PAUSED_UNTIL_FILE.write_text("not-an-iso-timestamp")
    assert pause.is_active() is False
    assert not pause.PAUSED_UNTIL_FILE.exists()
