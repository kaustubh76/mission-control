"""
Market session clocks.

Returns the current time and OPEN/CLOSED status for the four trader
sessions the dashboard shows: Tokyo, London, New York, India.
Trading is allowed 24h (crypto), but the session status drives UI colors.
"""

from datetime import datetime

import pytz

# Session windows in *local* exchange time (24h clock).
# Reference: ICT killzones.
SESSION_HOURS = {
    "tokyo": {"tz": "Asia/Tokyo", "open": 9, "close": 15},
    "london": {"tz": "Europe/London", "open": 8, "close": 16},
    "newyork": {"tz": "America/New_York", "open": 8, "close": 17},
}


def _session_status(name: str, at: datetime | None = None) -> tuple[str, str]:
    """Return (HH:MM:SS, 'OPEN' | 'CLOSED') for a named session.

    `at` is an optional reference timestamp (any timezone, including
    naïve = treat as UTC). When omitted, uses real wall-clock now —
    correct for the live scanner, wrong for backtesting (E5 in ROADMAP).
    """
    cfg = SESSION_HOURS[name]
    tz = pytz.timezone(cfg["tz"])
    if at is None:
        local = datetime.now(tz)
    else:
        # Naïve timestamps from pandas come back as UTC (the parquet cache
        # stores ms since epoch). Localize then convert.
        ref = at if at.tzinfo is not None else pytz.UTC.localize(at)
        local = ref.astimezone(tz)
    status = "OPEN" if cfg["open"] <= local.hour < cfg["close"] else "CLOSED"
    return local.strftime("%H:%M:%S"), status


def get_sessions(at: datetime | None = None) -> dict:
    """Return all the session/time fields the dashboard expects.

    `at` lets backtesting pass the bar's timestamp so killzone gating
    reflects the historical wall-clock at that bar, not the wall-clock
    at backtest-run time. Live callers should omit `at` to default to
    real wall-clock now (E5 in ROADMAP).
    """
    if at is None:
        india_now = datetime.now(pytz.timezone("Asia/Kolkata"))
    else:
        ref = at if at.tzinfo is not None else pytz.UTC.localize(at)
        india_now = ref.astimezone(pytz.timezone("Asia/Kolkata"))

    tokyo_time, tokyo_status = _session_status("tokyo", at=at)
    london_time, london_status = _session_status("london", at=at)
    ny_time, ny_status = _session_status("newyork", at=at)

    # active_session = whichever is currently OPEN (priority: NY > London > Tokyo)
    if ny_status == "OPEN":
        active = "NEW YORK"
    elif london_status == "OPEN":
        active = "LONDON"
    elif tokyo_status == "OPEN":
        active = "TOKYO"
    else:
        active = "OFF HOURS (24H CRYPTO)"

    return {
        "india_time": india_now.strftime("%H:%M:%S"),
        "tokyo_time": tokyo_time,
        "tokyo_status": tokyo_status,
        "london_time": london_time,
        "london_status": london_status,
        "newyork_time": ny_time,
        "newyork_status": ny_status,
        "active_session": active,
        # Crypto trades 24/7, so we never block on session.
        # Killzone gating is opt-in via Strategy(killzone_required=True).
        "allow_trade": True,
        "killzone_active": london_status == "OPEN" or ny_status == "OPEN",
    }


def is_killzone_active(session: dict | None = None) -> bool:
    """True when either London or NY is open — the high-liquidity ICT
    killzones. Pass an existing session dict to avoid recomputing.
    """
    s = session if session is not None else get_sessions()
    return bool(s.get("killzone_active", False))
