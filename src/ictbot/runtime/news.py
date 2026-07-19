"""
Helpers over the ForexFactory feed for strategy + UI consumption.

Pure functions on top of `ictbot.data.forex_factory`. No I/O caching here —
that's the data layer's job. These wrap the event list with the queries
strategy/telegram actually need:

    events_within(window_min, country=..., impact=...)
    high_impact_today(country='USD')
    next_event_eta(country='USD', impact=...)
    is_blackout(window_min, country=..., impact=...)

`now` is injectable so strategy tests (which freeze time) stay deterministic.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from ictbot.data.forex_factory import NewsEvent, fetch_events

DEFAULT_BLACKOUT_IMPACTS = ("High",)
DEFAULT_BLACKOUT_COUNTRIES = ("USD",)

# Process-local cache. Shorter TTL than the on-disk cache (1 h) so that a
# fresh scanner loop benefits from a single feed read across all pairs but
# still picks up a daily news update reasonably promptly.
_PROCESS_CACHE_TTL = 300.0  # 5 minutes
_process_cache: dict = {"ts": 0.0, "events": [], "error": None}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_cached_events(max_age: float = _PROCESS_CACHE_TTL) -> list[NewsEvent]:
    """Return events using a short-lived in-memory cache.

    Inside the TTL the cached list is returned with zero I/O. After the TTL
    we hit `fetch_events()` (which itself has a 1 h disk cache + 12 h stale
    fallback). If the fetch raises and we have NO previously-cached events,
    re-raise — callers (strategy gate, telegram surfacer) decide what to do.
    """
    now = time.time()
    if (now - _process_cache["ts"] < max_age) and _process_cache["events"]:
        return _process_cache["events"]
    try:
        events = fetch_events()
    except Exception:
        if _process_cache["events"]:
            return _process_cache["events"]  # serve stale rather than crash
        raise
    _process_cache["ts"] = now
    _process_cache["events"] = events
    _process_cache["error"] = None
    return events


def refresh_news(max_age: float = _PROCESS_CACHE_TTL) -> list[NewsEvent]:
    """Explicit warm-up hook for callers that fan out over many pairs.

    The scanner can call this once at the top of each loop iteration so
    all subsequent pair evaluations share a single, fresh feed read.
    """
    return get_cached_events(max_age=max_age)


def _reset_cache_for_tests() -> None:
    """Test helper — wipe the process cache so each test starts clean."""
    _process_cache["ts"] = 0.0
    _process_cache["events"] = []
    _process_cache["error"] = None


def _filter(
    events: list[NewsEvent],
    *,
    country: str | tuple[str, ...] | None = None,
    impact: str | tuple[str, ...] | None = None,
) -> list[NewsEvent]:
    """Apply country + impact filters; either may be a single value or tuple."""
    if isinstance(country, str):
        country = (country,)
    if isinstance(impact, str):
        impact = (impact,)
    out = events
    if country:
        cs = {c.upper() for c in country}
        out = [e for e in out if e.country in cs]
    if impact:
        ims = {i.lower() for i in impact}
        out = [e for e in out if e.impact.lower() in ims]
    return out


def events_within(
    window_min: float,
    *,
    country: str | tuple[str, ...] | None = None,
    impact: str | tuple[str, ...] | None = None,
    now: datetime | None = None,
    events: list[NewsEvent] | None = None,
) -> list[NewsEvent]:
    """Events whose timestamp lies in [now − window, now + window]."""
    now = now or _utcnow()
    events = events if events is not None else get_cached_events()
    events = _filter(events, country=country, impact=impact)
    lo = now - timedelta(minutes=window_min)
    hi = now + timedelta(minutes=window_min)
    return [e for e in events if lo <= e.ts <= hi]


def high_impact_today(
    *,
    country: str | tuple[str, ...] | None = "USD",
    now: datetime | None = None,
    events: list[NewsEvent] | None = None,
) -> list[NewsEvent]:
    """High-impact events whose UTC date == now's UTC date."""
    now = now or _utcnow()
    events = events if events is not None else get_cached_events()
    events = _filter(events, country=country, impact="High")
    today = now.date()
    return [e for e in events if e.ts.date() == today]


def next_event(
    *,
    country: str | tuple[str, ...] | None = None,
    impact: str | tuple[str, ...] | None = None,
    now: datetime | None = None,
    events: list[NewsEvent] | None = None,
) -> NewsEvent | None:
    """The next future event matching filters, or None."""
    now = now or _utcnow()
    events = events if events is not None else get_cached_events()
    events = _filter(events, country=country, impact=impact)
    upcoming = [e for e in events if e.ts > now]
    return upcoming[0] if upcoming else None  # events are pre-sorted


def next_event_eta(
    *,
    country: str | tuple[str, ...] | None = None,
    impact: str | tuple[str, ...] | None = None,
    now: datetime | None = None,
    events: list[NewsEvent] | None = None,
) -> tuple[NewsEvent, timedelta] | None:
    """(event, time-until-event) for the next match, or None."""
    now = now or _utcnow()
    e = next_event(country=country, impact=impact, now=now, events=events)
    return (e, e.ts - now) if e else None


def is_blackout(
    window_min: float,
    *,
    country: str | tuple[str, ...] | None = DEFAULT_BLACKOUT_COUNTRIES,
    impact: str | tuple[str, ...] | None = DEFAULT_BLACKOUT_IMPACTS,
    now: datetime | None = None,
    events: list[NewsEvent] | None = None,
) -> NewsEvent | None:
    """If a blacklist-matching event is within ±window minutes, return it.

    Returned event = caller's reason for blocking ('CPI in 8 min').
    None = clear to trade.
    """
    now = now or _utcnow()
    hits = events_within(
        window_min,
        country=country,
        impact=impact,
        now=now,
        events=events,
    )
    if not hits:
        return None
    # Pick the closest one (smallest |Δt|) for the reason string.
    return min(hits, key=lambda e: abs((e.ts - now).total_seconds()))
