"""
J10 (audit gap #18) — heartbeat liveness signal.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ictbot.runtime import heartbeat


@pytest.fixture(autouse=True)
def _tmp_heartbeat(tmp_path, monkeypatch):
    """Point the heartbeat at a tmp file per test."""
    path = tmp_path / "heartbeat.ts"
    monkeypatch.setattr(heartbeat, "HEARTBEAT_FILE", path)
    yield path


def test_no_heartbeat_means_stale(_tmp_heartbeat):
    assert heartbeat.last_beat() is None
    assert heartbeat.age_seconds() is None
    assert heartbeat.is_stale(max_age_seconds=60) is True


def test_beat_writes_current_timestamp(_tmp_heartbeat):
    heartbeat.beat()
    last = heartbeat.last_beat()
    assert last is not None
    assert abs(last - time.time()) < 1.0


def test_beat_atomically_replaces_existing_file(_tmp_heartbeat):
    """A second beat() overwrites the first cleanly (no .tmp left behind)."""
    heartbeat.beat(now=1000.0)
    heartbeat.beat(now=2000.0)
    assert heartbeat.last_beat() == 2000.0
    # Ensure no orphan .tmp.
    parent: Path = _tmp_heartbeat.parent
    leftovers = [p for p in parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_is_stale_threshold(_tmp_heartbeat):
    heartbeat.beat(now=time.time() - 100)  # 100s ago
    assert heartbeat.is_stale(max_age_seconds=50) is True
    assert heartbeat.is_stale(max_age_seconds=200) is False


def test_age_seconds_positive_after_recent_beat(_tmp_heartbeat):
    heartbeat.beat(now=time.time() - 10)
    age = heartbeat.age_seconds()
    assert age is not None
    assert 9 <= age <= 11


def test_corrupt_heartbeat_file_treated_as_stale(_tmp_heartbeat):
    _tmp_heartbeat.write_text("not a number\n", encoding="utf-8")
    assert heartbeat.last_beat() is None
    assert heartbeat.is_stale(max_age_seconds=60) is True
