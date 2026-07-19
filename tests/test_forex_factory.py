"""Offline tests for the ForexFactory parser + cache. No network."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ictbot.data import forex_factory as ff

FIXTURE = Path(__file__).parent / "fixtures" / "ff_calendar_sample.xml"


def _fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


# -----------------------------------------------------------------------------
# parse_xml
# -----------------------------------------------------------------------------


def test_parse_returns_events():
    events = ff.parse_xml(_fixture_bytes())
    assert len(events) > 50, "fixture should have many events"
    assert all(isinstance(e, ff.NewsEvent) for e in events)


def test_parse_known_event_present():
    """Spot-check the USD Core PCE event known to be in the fixture."""
    events = ff.parse_xml(_fixture_bytes())
    pces = [e for e in events if "Core PCE" in e.title and e.country == "USD"]
    assert pces, "expected USD Core PCE event in fixture"
    e = pces[0]
    assert e.impact == "High"
    assert e.forecast and e.previous
    assert e.ts.tzinfo is not None, "ts must be timezone-aware"
    assert e.ts.utcoffset() == timezone.utc.utcoffset(None)


def test_parse_events_are_sorted_ascending():
    events = ff.parse_xml(_fixture_bytes())
    for a, b in zip(events, events[1:], strict=False):
        assert a.ts <= b.ts


def test_parse_filters_out_unparseable_times():
    """Synthetic XML with 'All Day' time — should be dropped, not crash."""
    xml = (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<weeklyevents>"
        "  <event>"
        "    <title>Bad Event</title>"
        "    <country>USD</country>"
        "    <date><![CDATA[05-28-2026]]></date>"
        "    <time><![CDATA[All Day]]></time>"
        "    <impact>High</impact>"
        "    <forecast/><previous/><url/>"
        "  </event>"
        "  <event>"
        "    <title>Good Event</title>"
        "    <country>USD</country>"
        "    <date><![CDATA[05-28-2026]]></date>"
        "    <time><![CDATA[12:30pm]]></time>"
        "    <impact>High</impact>"
        "    <forecast/><previous/><url/>"
        "  </event>"
        "</weeklyevents>"
    )
    events = ff.parse_xml(xml)
    assert len(events) == 1
    assert events[0].title == "Good Event"


def test_et_to_utc_conversion():
    """12:30pm ET on 05-28-2026 must convert to the correct UTC moment.

    Late May = EDT (UTC-4), so 12:30 ET = 16:30 UTC.
    """
    ts = ff._parse_time_et("05-28-2026", "12:30pm")
    assert ts is not None
    assert ts == datetime(2026, 5, 28, 16, 30, tzinfo=timezone.utc)


def test_to_dict_round_trip():
    events = ff.parse_xml(_fixture_bytes())
    e = events[0]
    rt = ff.NewsEvent.from_dict(e.to_dict())
    assert rt == e


# -----------------------------------------------------------------------------
# fetch_events caching behaviour (network is monkey-patched out)
# -----------------------------------------------------------------------------


def _make_cache(path: Path, age_seconds: float, events: list[ff.NewsEvent]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": time.time() - age_seconds,
                "events": [e.to_dict() for e in events],
            }
        )
    )


def test_fresh_cache_skips_network(tmp_path, monkeypatch):
    cache = tmp_path / "news.json"
    events = ff.parse_xml(_fixture_bytes())
    _make_cache(cache, age_seconds=60, events=events[:5])

    def boom(*a, **kw):
        raise AssertionError("network should not be called when cache is fresh")

    monkeypatch.setattr(ff.requests, "get", boom)

    out = ff.fetch_events(cache_path=cache)
    assert [e.title for e in out] == [e.title for e in events[:5]]


def test_stale_cache_falls_back_on_network_error(tmp_path, monkeypatch):
    cache = tmp_path / "news.json"
    events = ff.parse_xml(_fixture_bytes())
    # Cache is older than FRESH_TTL but younger than STALE_OK_TTL.
    _make_cache(cache, age_seconds=ff.FRESH_TTL + 10, events=events[:3])

    def boom(*a, **kw):
        raise ff.requests.ConnectionError("offline")

    monkeypatch.setattr(ff.requests, "get", boom)

    out = ff.fetch_events(cache_path=cache)
    assert len(out) == 3


def test_no_cache_and_network_failure_raises(tmp_path, monkeypatch):
    cache = tmp_path / "nope.json"  # does not exist

    def boom(*a, **kw):
        raise ff.requests.ConnectionError("offline")

    monkeypatch.setattr(ff.requests, "get", boom)

    with pytest.raises(RuntimeError, match="no usable cache"):
        ff.fetch_events(cache_path=cache)


def test_successful_fetch_writes_cache(tmp_path, monkeypatch):
    cache = tmp_path / "fresh.json"

    class FakeResp:
        content = _fixture_bytes()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(ff.requests, "get", lambda *a, **kw: FakeResp())
    out = ff.fetch_events(cache_path=cache)
    assert cache.exists()
    assert len(out) > 50

    blob = json.loads(cache.read_text())
    assert blob["fetched_at"] <= time.time()
    assert len(blob["events"]) == len(out)
