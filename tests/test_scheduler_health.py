"""scheduler_card — the silent-cron-death signal: fresh vs stale live-tick + dd-watch ages."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import ictbot.api.reads as reads


def _iso(dt):
    return dt.isoformat()


def _setup(tmp_path, monkeypatch, *, live_dt=None, dd_line_dt=None):
    monkeypatch.setattr(reads, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(reads, "DATA_DIR", tmp_path)
    if live_dt is not None:
        (tmp_path / "allocator_live.jsonl").write_text(
            json.dumps({"event": "REBALANCE", "ts": _iso(live_dt), "nav_after": 7.9}) + "\n"
        )
    if dd_line_dt is not None:
        logs = tmp_path / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "dd_watch_live.log").write_text(
            f"bash: dd_watch.sh: Operation not permitted\n[{_iso(dd_line_dt)}] dd-watch OK: NAV=7.9 dd=1.6% <= cap=5.0%.\n"
        )


def test_healthy_when_both_fresh(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    _setup(tmp_path, monkeypatch, live_dt=now - timedelta(hours=3), dd_line_dt=now - timedelta(minutes=12))
    c = reads.scheduler_card()
    assert c["ok"] is True
    assert c["live_tick_stale"] is False and c["dd_watch_stale"] is False
    assert c["live_tick_age_h"] == 3.0 and 11.0 <= c["dd_watch_age_m"] <= 13.0


def test_stale_live_tick_flags_not_ok(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    _setup(tmp_path, monkeypatch, live_dt=now - timedelta(hours=30), dd_line_dt=now - timedelta(minutes=5))
    c = reads.scheduler_card()
    assert c["ok"] is False and c["live_tick_stale"] is True and c["dd_watch_stale"] is False


def test_stale_dd_watch_flags_not_ok(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    _setup(tmp_path, monkeypatch, live_dt=now - timedelta(hours=2), dd_line_dt=now - timedelta(minutes=90))
    c = reads.scheduler_card()
    assert c["ok"] is False and c["dd_watch_stale"] is True


def test_missing_artifacts_degrade_to_stale(tmp_path, monkeypatch):
    # fail-loud: no journal / no log → both stale, ok False (never a silent false-green)
    _setup(tmp_path, monkeypatch)
    c = reads.scheduler_card()
    assert c["ok"] is False and c["live_tick_age_h"] is None and c["dd_watch_age_m"] is None
