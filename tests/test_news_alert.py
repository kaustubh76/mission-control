"""Unit tests for the standalone news-aware Telegram alerter.

No network: monkey-patch `_news.next_event_eta` to return synthetic events.
Send is captured into a list rather than going to Telegram.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ictbot.data.forex_factory import NewsEvent
from ictbot.notify import news_alert as A
from ictbot.runtime import news as _news


def _ev(when: datetime, *, country="USD", impact="High", title="Sample") -> NewsEvent:
    return NewsEvent(
        title=title,
        country=country,
        impact=impact,
        ts=when,
        forecast="0.3%",
        previous="0.2%",
        url="",
    )


def _now() -> datetime:
    # Anchored to the REAL clock (top of the current hour): news_alert._save_alerted
    # prunes entries older than PRUNE_AFTER_DAYS against wall-clock now, so a frozen
    # calendar date silently expires the dedup entry the moment it is saved. All
    # events are built at fixed offsets from this, so assertions stay deterministic.
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def tmp_alerts(tmp_path, monkeypatch):
    """Redirect the dedup store into a tmpdir so tests don't pollute repo state."""
    fake_file = tmp_path / "news_alerts.json"
    monkeypatch.setattr(A, "ALERTS_FILE", fake_file)
    yield fake_file


class _SendCapture:
    def __init__(self):
        self.msgs: list[str] = []

    def __call__(self, m: str) -> bool:
        self.msgs.append(m)
        return True


# -----------------------------------------------------------------------------


def test_fires_when_event_inside_window(tmp_alerts, monkeypatch):
    now = _now()
    ev = _ev(now + timedelta(minutes=42), title="CPI")
    monkeypatch.setattr(_news, "next_event_eta", lambda **kw: (ev, ev.ts - now))

    sent = _SendCapture()
    out = A.check_and_alert(window_min=60, send_fn=sent, now=now)
    assert out is ev
    assert len(sent.msgs) == 1
    assert "CPI" in sent.msgs[0]
    assert "+42 min" in sent.msgs[0]


def test_does_not_fire_outside_window(tmp_alerts, monkeypatch):
    now = _now()
    ev = _ev(now + timedelta(minutes=90))
    monkeypatch.setattr(_news, "next_event_eta", lambda **kw: (ev, ev.ts - now))

    sent = _SendCapture()
    out = A.check_and_alert(window_min=60, send_fn=sent, now=now)
    assert out is None
    assert sent.msgs == []


def test_deduplicates_within_a_run(tmp_alerts, monkeypatch):
    """Two calls back-to-back for the same event must alert exactly once."""
    now = _now()
    ev = _ev(now + timedelta(minutes=42), title="CPI")
    monkeypatch.setattr(_news, "next_event_eta", lambda **kw: (ev, ev.ts - now))

    sent = _SendCapture()
    A.check_and_alert(window_min=60, send_fn=sent, now=now)
    A.check_and_alert(window_min=60, send_fn=sent, now=now)
    A.check_and_alert(window_min=60, send_fn=sent, now=now)
    assert len(sent.msgs) == 1


def test_deduplicates_across_processes(tmp_alerts, monkeypatch):
    """The dedup file is the durable store — write/read survives 'restart'."""
    now = _now()
    ev = _ev(now + timedelta(minutes=42), title="CPI")
    monkeypatch.setattr(_news, "next_event_eta", lambda **kw: (ev, ev.ts - now))

    sent = _SendCapture()
    A.check_and_alert(window_min=60, send_fn=sent, now=now)
    assert tmp_alerts.exists()
    # Confirm the file content matches our key
    state = json.loads(tmp_alerts.read_text())
    assert any("CPI" in k for k in state.keys())

    # Second "process" should see the same file and skip the alert.
    sent2 = _SendCapture()
    A.check_and_alert(window_min=60, send_fn=sent2, now=now)
    assert sent2.msgs == []


def test_different_events_each_alert_once(tmp_alerts, monkeypatch):
    now = _now()
    events_iter = iter(
        [
            (_ev(now + timedelta(minutes=20), title="CPI"), timedelta(minutes=20)),
            (_ev(now + timedelta(minutes=45), title="FOMC"), timedelta(minutes=45)),
        ]
    )

    def fake(**kw):
        return next(events_iter, None)

    monkeypatch.setattr(_news, "next_event_eta", fake)

    sent = _SendCapture()
    A.check_and_alert(window_min=60, send_fn=sent, now=now)
    A.check_and_alert(window_min=60, send_fn=sent, now=now)
    assert len(sent.msgs) == 2
    assert "CPI" in sent.msgs[0]
    assert "FOMC" in sent.msgs[1]


def test_send_failure_does_not_pollute_dedup_store(tmp_alerts, monkeypatch):
    """If Telegram returns False, we MUST NOT record the alert — caller
    should retry next time. Stale-write-on-failure is the bug we're guarding
    against."""
    now = _now()
    ev = _ev(now + timedelta(minutes=42))
    monkeypatch.setattr(_news, "next_event_eta", lambda **kw: (ev, ev.ts - now))

    sent_failure = lambda m: False
    out = A.check_and_alert(window_min=60, send_fn=sent_failure, now=now)
    assert out is None
    # File either absent or has no entry for this event
    state = json.loads(tmp_alerts.read_text()) if tmp_alerts.exists() else {}
    assert not any("Sample" in k for k in state.keys())


def test_feed_unavailable_returns_none_gracefully(tmp_alerts, monkeypatch):
    """When the feed itself errors out we log + return None, not raise."""

    def boom(**kw):
        raise RuntimeError("FF down")

    monkeypatch.setattr(_news, "next_event_eta", boom)
    sent = _SendCapture()
    out = A.check_and_alert(window_min=60, send_fn=sent, now=_now())
    assert out is None
    assert sent.msgs == []


def test_prune_drops_old_entries(tmp_alerts):
    """Entries older than PRUNE_AFTER_DAYS are removed on save."""
    long_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    tmp_alerts.write_text(
        json.dumps(
            {
                "old_key": long_ago,
                "recent_key": yesterday,
            }
        )
    )
    # Re-save through the loader, which prunes.
    state = A._load_alerted()
    A._save_alerted(state)
    persisted = json.loads(tmp_alerts.read_text())
    assert "old_key" not in persisted
    assert "recent_key" in persisted
