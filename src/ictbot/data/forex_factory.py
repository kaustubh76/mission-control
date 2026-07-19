"""
ForexFactory economic-calendar fetcher.

Pulls the public weekly calendar XML, parses it into typed `NewsEvent`s, and
caches the result to `data/cache/news.json`. Designed to fail gracefully:
if the live feed is unreachable but we have a recent cache, return the cache.

This module is intentionally I/O-bound and pure-data — strategy code stays
unaware of the network. Callers compose it via `runtime/news.py`
(to be added in step 2 of the plan).

Schema reference (one event):
    <event>
      <title>Core PCE Price Index m/m</title>
      <country>USD</country>
      <date><![CDATA[05-28-2026]]></date>
      <time><![CDATA[12:30pm]]></time>           ← ALWAYS in Eastern Time (ET)
      <impact><![CDATA[High]]></impact>          ← High / Medium / Low / Holiday
      <forecast>0.3%</forecast>
      <previous>0.3%</previous>
      <url>https://...</url>
    </event>
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests

from ictbot.settings import CACHE_DIR

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
ET_TZ = ZoneInfo("America/New_York")  # FF times are ET (handles DST itself)
DEFAULT_CACHE = CACHE_DIR / "news.json"

# Cache freshness policy:
#   < FRESH_TTL    → serve cache without hitting network
#   < STALE_OK_TTL → if network fails, fall back to cache
#   >= STALE_OK_TTL → cache is too old, propagate the error
FRESH_TTL = 3600  # 1 hour
STALE_OK_TTL = 12 * 3600  # 12 hours


# -----------------------------------------------------------------------------
# Data shape
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsEvent:
    title: str
    country: str  # USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF, CNY
    impact: str  # High / Medium / Low / Holiday
    ts: datetime  # UTC-aware
    forecast: str
    previous: str
    url: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.astimezone(timezone.utc).isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> NewsEvent:
        return cls(
            title=d["title"],
            country=d["country"],
            impact=d["impact"],
            ts=datetime.fromisoformat(d["ts"]),
            forecast=d.get("forecast", ""),
            previous=d.get("previous", ""),
            url=d.get("url", ""),
        )


# -----------------------------------------------------------------------------
# XML → events
# -----------------------------------------------------------------------------


def _parse_time_et(date_s: str, time_s: str) -> datetime | None:
    """`'05-28-2026' + '12:30pm'` → UTC-aware datetime. None if unparseable."""
    if not date_s or not time_s:
        return None
    t = time_s.strip().lower()
    # FF emits "All Day" / "Tentative" / "Day 1" for some events; we can't
    # blackout-window an event without a real time, so we skip them.
    if t in {"all day", "tentative"} or "day" in t:
        return None
    try:
        local = datetime.strptime(f"{date_s.strip()} {t}", "%m-%d-%Y %I:%M%p")
    except ValueError:
        return None
    return local.replace(tzinfo=ET_TZ).astimezone(timezone.utc)


def parse_xml(content: bytes | str) -> list[NewsEvent]:
    """Parse FF XML payload into a list of `NewsEvent`. Never raises on bad rows."""
    if isinstance(content, bytes):
        # FF declares windows-1252; ElementTree honours the XML declaration.
        text = content.decode("cp1252", errors="replace")
    else:
        text = content
    root = ET.fromstring(text)

    events: list[NewsEvent] = []
    for ev in root.findall("event"):
        title = (ev.findtext("title") or "").strip()
        country = (ev.findtext("country") or "").strip().upper()
        impact = (ev.findtext("impact") or "").strip()
        date_s = (ev.findtext("date") or "").strip()
        time_s = (ev.findtext("time") or "").strip()
        forecast = (ev.findtext("forecast") or "").strip()
        previous = (ev.findtext("previous") or "").strip()
        url = (ev.findtext("url") or "").strip()

        ts = _parse_time_et(date_s, time_s)
        if ts is None or not title or not country:
            continue

        events.append(
            NewsEvent(
                title=title,
                country=country,
                impact=impact,
                ts=ts,
                forecast=forecast,
                previous=previous,
                url=url,
            )
        )
    events.sort(key=lambda e: e.ts)
    return events


# -----------------------------------------------------------------------------
# Fetch with cache
# -----------------------------------------------------------------------------


def _read_cache(path: Path) -> tuple[list[NewsEvent], float] | None:
    """Return (events, age_seconds) or None if no cache."""
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text())
        events = [NewsEvent.from_dict(d) for d in blob["events"]]
        age = time.time() - blob.get("fetched_at", 0)
        return events, age
    except Exception:
        return None


def _write_cache(path: Path, events: list[NewsEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"fetched_at": time.time(), "events": [e.to_dict() for e in events]},
            indent=2,
        )
    )


def fetch_events(
    *,
    cache_path: Path = DEFAULT_CACHE,
    fresh_ttl: int = FRESH_TTL,
    stale_ok_ttl: int = STALE_OK_TTL,
    timeout: float | tuple[float, float] = (5.0, 10.0),
) -> list[NewsEvent]:
    """Return this week's FF events, hitting the cache when fresh enough.

    Timeout is a (connect, read) tuple — without an explicit connect
    timeout, requests can sit in SYN_SENT for many minutes when the
    macOS resolver picks an IPv6 endpoint that the network can't reach.
    A 5s connect ceiling lets urllib3 fall through to the next resolved
    address (usually an IPv4) instead of hanging the scan loop.
    """
    cached = _read_cache(cache_path)
    if cached and cached[1] < fresh_ttl:
        return cached[0]

    try:
        r = requests.get(
            FF_URL, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (ictbot/1.0)"}
        )
        r.raise_for_status()
        events = parse_xml(r.content)
        _write_cache(cache_path, events)
        return events
    except Exception as e:
        if cached and cached[1] < stale_ok_ttl:
            return cached[0]
        raise RuntimeError(
            f"ForexFactory fetch failed and no usable cache "
            f"(cache age: {cached[1] if cached else 'none'}s): {e}"
        ) from e


# -----------------------------------------------------------------------------
# CLI for ad-hoc inspection
# -----------------------------------------------------------------------------


def _cli() -> None:
    import argparse

    ap = argparse.ArgumentParser(prog="ictbot.data.forex_factory")
    ap.add_argument("--country", default=None, help="filter (e.g. USD)")
    ap.add_argument("--impact", default=None, help="filter (High/Medium/Low/Holiday)")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    events = fetch_events()
    if args.country:
        events = [e for e in events if e.country == args.country.upper()]
    if args.impact:
        events = [e for e in events if e.impact.lower() == args.impact.lower()]
    for e in events[: args.limit]:
        print(
            f"{e.ts.strftime('%a %b %d  %H:%M UTC')}   "
            f"{e.country:3s}  [{e.impact:6s}]   "
            f"{e.title}   "
            f"(fcst={e.forecast or '—'}, prev={e.previous or '—'})"
        )


if __name__ == "__main__":
    _cli()
