"""
Standalone news-aware Telegram alert.

Fires ONCE when a high-impact macro event enters a configurable window
(default 60 minutes). De-duplicates on `{date}_{title}` so even a 30-second
scanner loop only pings you a single time per event.

Two ways to use it:

  1. Ad-hoc (run it yourself or via cron every few minutes):
        python -m ictbot.notify.news_alert
        make news_alert

  2. Embedded in the scanner loop (when NEWS_ALERT_ENABLED=true in .env):
        scanner.py calls `check_and_alert()` once per loop iteration.

The dedup store lives at `data/journal/news_alerts.json`. Entries older
than 7 days are pruned on each save so the file stays small.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from ictbot.data.forex_factory import NewsEvent
from ictbot.notify.telegram import send_telegram
from ictbot.runtime import news as _news
from ictbot.settings import (
    JOURNAL_DIR,
    NEWS_BLACKOUT_COUNTRIES,
    NEWS_BLACKOUT_IMPACTS,
    NEWS_BLACKOUT_MINUTES,
)

ALERTS_FILE = JOURNAL_DIR / "news_alerts.json"
DEFAULT_WINDOW_MIN = 60.0
PRUNE_AFTER_DAYS = 7


# -----------------------------------------------------------------------------
# Dedup store
# -----------------------------------------------------------------------------


def _key(event: NewsEvent) -> str:
    return f"{event.ts.strftime('%Y-%m-%d')}_{event.country}_{event.title}"


def _load_alerted() -> dict[str, str]:
    """Read the dedup store. Returns {key: iso_alerted_at}."""
    if not ALERTS_FILE.exists():
        return {}
    try:
        return json.loads(ALERTS_FILE.read_text())
    except Exception:
        return {}


def _save_alerted(state: dict[str, str]) -> None:
    """Persist the dedup store; prune entries older than PRUNE_AFTER_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_AFTER_DAYS)
    pruned = {
        k: ts for k, ts in state.items() if _safe_parse(ts) is None or _safe_parse(ts) > cutoff
    }
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_FILE.write_text(json.dumps(pruned, indent=2))


def _safe_parse(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Message formatting
# -----------------------------------------------------------------------------


def _format_alert(event: NewsEvent, eta: timedelta) -> str:
    eta_min = eta.total_seconds() / 60.0
    when = event.ts.strftime("%Y-%m-%d %H:%M UTC")
    blackout_note = (
        f"   Bot will refuse trades within ±{NEWS_BLACKOUT_MINUTES:.0f} min."
        if NEWS_BLACKOUT_MINUTES > 0
        else "   NEWS_BLACKOUT_MINUTES = 0 — bot will NOT pause for this."
    )
    return (
        f"⚠ ICTBOT  ·  NEWS ALERT\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{event.country}  ·  {event.impact.upper()}\n"
        f"{event.title}\n\n"
        f"when     {when}\n"
        f"in       {eta_min:+.0f} min\n"
        f"forecast {event.forecast or '—'}\n"
        f"previous {event.previous or '—'}\n\n"
        f"{blackout_note}"
    )


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def check_and_alert(
    window_min: float = DEFAULT_WINDOW_MIN,
    *,
    countries: tuple[str, ...] | None = None,
    impacts: tuple[str, ...] | None = None,
    send_fn=send_telegram,
    events: list[NewsEvent] | None = None,
    now: datetime | None = None,
) -> NewsEvent | None:
    """If a matching event is within `window_min` and we haven't pinged for
    it yet, send the alert and record it. Returns the event that fired
    (or None when nothing fired).
    """
    countries = countries or NEWS_BLACKOUT_COUNTRIES
    impacts = impacts or NEWS_BLACKOUT_IMPACTS
    now = now or datetime.now(timezone.utc)

    try:
        hit = _news.next_event_eta(
            country=countries,
            impact=impacts,
            now=now,
            events=events,
        )
    except Exception as e:
        print(f"[news_alert] feed unavailable: {e}")
        return None

    if hit is None:
        return None
    event, eta = hit
    if eta.total_seconds() / 60.0 > window_min:
        return None  # too far away — wait

    state = _load_alerted()
    k = _key(event)
    if k in state:
        return None  # already alerted

    msg = _format_alert(event, eta)
    sent = send_fn(msg)
    if sent:
        state[k] = now.isoformat()
        _save_alerted(state)
        return event
    return None


def main() -> None:
    ap = argparse.ArgumentParser(prog="ictbot.notify.news_alert")
    ap.add_argument(
        "--window-min",
        type=float,
        default=DEFAULT_WINDOW_MIN,
        help="alert when next event is within this many minutes",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print alert to stdout instead of sending"
    )
    ap.add_argument("--reset", action="store_true", help="wipe the dedup store and exit")
    args = ap.parse_args()

    if args.reset:
        if ALERTS_FILE.exists():
            ALERTS_FILE.unlink()
        print("dedup store cleared.")
        return

    send_fn = (lambda m: (print(m), True)[1]) if args.dry_run else send_telegram
    fired = check_and_alert(window_min=args.window_min, send_fn=send_fn)
    if fired:
        print(f"alerted: {fired.country} {fired.title} at {fired.ts.isoformat()}")
    else:
        print("no event in window (or already alerted)")


if __name__ == "__main__":
    main()
