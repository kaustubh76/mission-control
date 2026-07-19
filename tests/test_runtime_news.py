"""Pure unit tests for runtime/news.py — no network, no I/O."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ictbot.data.forex_factory import NewsEvent
from ictbot.runtime import news as N


def _ev(
    when: datetime, country: str = "USD", impact: str = "High", title: str = "Sample"
) -> NewsEvent:
    return NewsEvent(
        title=title, country=country, impact=impact, ts=when, forecast="", previous="", url=""
    )


def _now() -> datetime:
    return datetime(2026, 5, 28, 16, 0, tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# events_within
# -----------------------------------------------------------------------------


def test_events_within_includes_past_and_future_in_window():
    now = _now()
    evs = [
        _ev(now - timedelta(minutes=10)),
        _ev(now + timedelta(minutes=5)),
        _ev(now + timedelta(minutes=60)),  # outside ±15
    ]
    out = N.events_within(15, now=now, events=evs)
    assert len(out) == 2


def test_events_within_filters_by_country():
    now = _now()
    evs = [
        _ev(now + timedelta(minutes=5), country="USD"),
        _ev(now + timedelta(minutes=5), country="EUR"),
    ]
    out = N.events_within(15, country="USD", now=now, events=evs)
    assert [e.country for e in out] == ["USD"]


def test_events_within_accepts_tuple_filters():
    now = _now()
    evs = [_ev(now, country="USD"), _ev(now, country="GBP"), _ev(now, country="JPY")]
    out = N.events_within(15, country=("USD", "GBP"), now=now, events=evs)
    assert {e.country for e in out} == {"USD", "GBP"}


# -----------------------------------------------------------------------------
# high_impact_today
# -----------------------------------------------------------------------------


def test_high_impact_today_filters_by_date_and_impact():
    now = _now()
    evs = [
        _ev(now - timedelta(hours=2), impact="High"),  # today, High ✓
        _ev(now + timedelta(hours=2), impact="Medium"),  # today, Medium ✗
        _ev(now + timedelta(days=1), impact="High"),  # tomorrow ✗
    ]
    out = N.high_impact_today(now=now, events=evs)
    assert len(out) == 1


# -----------------------------------------------------------------------------
# next_event / next_event_eta
# -----------------------------------------------------------------------------


def test_next_event_returns_earliest_future():
    now = _now()
    evs = [
        _ev(now - timedelta(minutes=30), title="past"),
        _ev(now + timedelta(minutes=10), title="soon"),
        _ev(now + timedelta(hours=4), title="later"),
    ]
    e = N.next_event(now=now, events=evs)
    assert e.title == "soon"


def test_next_event_none_when_no_future():
    now = _now()
    evs = [_ev(now - timedelta(hours=1))]
    assert N.next_event(now=now, events=evs) is None


def test_next_event_eta_is_positive():
    now = _now()
    evs = [_ev(now + timedelta(minutes=12))]
    e, dt = N.next_event_eta(now=now, events=evs)
    assert dt.total_seconds() == pytest.approx(12 * 60, abs=1)


# -----------------------------------------------------------------------------
# is_blackout
# -----------------------------------------------------------------------------


def test_blackout_triggers_inside_window():
    now = _now()
    evs = [_ev(now + timedelta(minutes=8), title="CPI")]
    e = N.is_blackout(15, now=now, events=evs)
    assert e is not None and e.title == "CPI"


def test_blackout_clear_outside_window():
    now = _now()
    evs = [_ev(now + timedelta(minutes=30), title="CPI")]
    assert N.is_blackout(15, now=now, events=evs) is None


def test_blackout_only_for_configured_country_and_impact():
    now = _now()
    evs = [
        _ev(now + timedelta(minutes=5), country="EUR", impact="High"),
        _ev(now + timedelta(minutes=5), country="USD", impact="Low"),
    ]
    # default filter is (USD, High) — neither event should trip it
    assert N.is_blackout(15, now=now, events=evs) is None


def test_blackout_picks_closest_event_when_multiple_match():
    now = _now()
    evs = [
        _ev(now + timedelta(minutes=14), title="far"),
        _ev(now - timedelta(minutes=2), title="closest"),
        _ev(now + timedelta(minutes=10), title="middle"),
    ]
    hit = N.is_blackout(15, now=now, events=evs)
    assert hit.title == "closest"


# -----------------------------------------------------------------------------
# Process cache (Step C)
# -----------------------------------------------------------------------------


class _Counter:
    """Counts how many times fetch_events is invoked."""

    def __init__(self, events):
        self.events = events
        self.n = 0

    def __call__(self):
        self.n += 1
        return self.events


def test_get_cached_events_calls_fetch_only_once_inside_ttl(monkeypatch):
    N._reset_cache_for_tests()
    counter = _Counter([_ev(_now())])
    monkeypatch.setattr(N, "fetch_events", counter)

    for _ in range(5):
        out = N.get_cached_events(max_age=60)
        assert len(out) == 1
    assert counter.n == 1, "5 calls inside TTL should hit the cache 4 times"


def test_get_cached_events_refreshes_after_ttl(monkeypatch):
    N._reset_cache_for_tests()
    counter = _Counter([_ev(_now())])
    monkeypatch.setattr(N, "fetch_events", counter)

    N.get_cached_events(max_age=60)
    # Force expiry by rewinding the cache timestamp.
    N._process_cache["ts"] -= 120
    N.get_cached_events(max_age=60)
    assert counter.n == 2


def test_get_cached_events_serves_stale_on_fetch_error(monkeypatch):
    N._reset_cache_for_tests()
    # Prime the cache once with a successful fetch.
    monkeypatch.setattr(N, "fetch_events", lambda: [_ev(_now(), title="primed")])
    N.get_cached_events(max_age=60)
    assert N._process_cache["events"][0].title == "primed"

    # Now make subsequent fetches raise. With cache primed, we serve stale.
    def boom():
        raise RuntimeError("FF down")

    monkeypatch.setattr(N, "fetch_events", boom)
    N._process_cache["ts"] -= 999  # force expiry
    out = N.get_cached_events(max_age=60)
    assert out and out[0].title == "primed"


def test_get_cached_events_raises_when_cache_empty_and_fetch_fails(monkeypatch):
    N._reset_cache_for_tests()

    def boom():
        raise RuntimeError("FF down")

    monkeypatch.setattr(N, "fetch_events", boom)
    with pytest.raises(RuntimeError):
        N.get_cached_events()


def test_is_blackout_uses_cached_events_when_no_explicit_list(monkeypatch):
    """The strategy gate doesn't pass events= — it must hit the cache."""
    N._reset_cache_for_tests()
    counter = _Counter(
        [_ev(_now() + timedelta(minutes=5), country="USD", impact="High", title="CPI")]
    )
    monkeypatch.setattr(N, "fetch_events", counter)

    # Two consecutive calls (e.g. two pairs in the same loop tick) should
    # share ONE fetch and both see the same blackout.
    hit1 = N.is_blackout(15, now=_now())
    hit2 = N.is_blackout(15, now=_now())
    assert hit1.title == "CPI" and hit2.title == "CPI"
    assert counter.n == 1
