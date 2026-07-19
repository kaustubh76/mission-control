"""
E5 (ROADMAP §E5) — get_sessions(at=...) honours an explicit timestamp
so backtests see the wall-clock of the BAR, not the wall-clock of
the moment the backtest started.

Without this fix, killzone gating is uniformly on/off across an entire
replay (whatever happened to be true when run_backtest was invoked).
The B4 gates-A/B experiment depends on E5 because otherwise the gate
is a constant factor over the run.
"""

from datetime import datetime

import pytz

from ictbot.runtime.sessions import _session_status, get_sessions

# Tokyo session: 09:00 - 15:00 Asia/Tokyo. UTC offset +9 → 00:00-06:00 UTC.
# London: 08:00 - 16:00 Europe/London. Winter offset 0 → 08:00-16:00 UTC.
# NY: 08:00 - 17:00 America/New_York. Winter offset -5 → 13:00-22:00 UTC.


def _utc(year, month, day, hour, minute=0):
    return pytz.UTC.localize(datetime(year, month, day, hour, minute))


def test_tokyo_open_at_specified_utc_morning():
    # 03:00 UTC on a January day → 12:00 Tokyo (inside 09-15 window).
    _, status = _session_status("tokyo", at=_utc(2026, 1, 15, 3))
    assert status == "OPEN"


def test_tokyo_closed_at_specified_utc_evening():
    # 18:00 UTC → 03:00 Tokyo (next day) — closed.
    _, status = _session_status("tokyo", at=_utc(2026, 1, 15, 18))
    assert status == "CLOSED"


def test_london_status_varies_with_bar_time():
    # 10:00 UTC, January → London 10:00 (inside 08-16 window) → OPEN.
    _, london_open = _session_status("london", at=_utc(2026, 1, 15, 10))
    assert london_open == "OPEN"

    # 22:00 UTC, January → London 22:00 → CLOSED.
    _, london_closed = _session_status("london", at=_utc(2026, 1, 15, 22))
    assert london_closed == "CLOSED"


def test_ny_status_varies_with_bar_time():
    # 14:00 UTC, January → NY 09:00 (winter time, -5) → OPEN.
    _, ny_open = _session_status("newyork", at=_utc(2026, 1, 15, 14))
    assert ny_open == "OPEN"

    # 03:00 UTC, January → NY 22:00 prior day → CLOSED.
    _, ny_closed = _session_status("newyork", at=_utc(2026, 1, 15, 3))
    assert ny_closed == "CLOSED"


def test_get_sessions_with_at_returns_full_dict_with_bar_time_status():
    s = get_sessions(at=_utc(2026, 1, 15, 14))
    assert s["newyork_status"] == "OPEN"
    assert s["london_status"] == "OPEN"  # 14:00 UTC = inside 08-16
    assert s["tokyo_status"] == "CLOSED"  # 14:00 UTC = 23:00 Tokyo
    assert s["active_session"] == "NEW YORK"
    assert s["killzone_active"] is True


def test_get_sessions_killzone_inactive_when_only_tokyo_open():
    # 03:00 UTC = 12:00 Tokyo (OPEN), London/NY both closed → no killzone.
    s = get_sessions(at=_utc(2026, 1, 15, 3))
    assert s["tokyo_status"] == "OPEN"
    assert s["killzone_active"] is False
    assert s["active_session"] == "TOKYO"


def test_get_sessions_no_at_uses_wall_clock_now():
    # When `at` is None, we get a populated dict; status values are
    # whatever they are right now — just check the contract.
    s = get_sessions()
    for key in ("tokyo_status", "london_status", "newyork_status", "active_session"):
        assert key in s
    assert s["allow_trade"] is True


def test_naive_timestamp_is_treated_as_utc():
    # The backtest engine passes pd.Timestamp(...).to_pydatetime() which
    # is naïve. We treat naïve as UTC so the math is unambiguous.
    naive = datetime(2026, 1, 15, 14)
    _, status = _session_status("newyork", at=naive)
    assert status == "OPEN"


# ---------------------------------------------------------------------------
# J12 (audit gap #20) — DST transitions must not silently shift killzones.
# Europe/London springs forward on the last Sun of March; America/New_York
# on the second Sun of March. The local-hour comparison in _session_status
# would mis-classify if we used a fixed UTC offset — pytz handles it
# correctly, but lock the invariant with tests so a future "simplify with
# UTC offset" refactor doesn't regress.
# ---------------------------------------------------------------------------


def test_london_session_pre_DST_winter_offset():
    """2026-03-29 02:00 UTC is BEFORE London's spring-forward (02:00 → 03:00).
    Local UK time is 02:00 → London CLOSED."""
    _, status = _session_status("london", at=_utc(2026, 3, 29, 1))
    assert status == "CLOSED"


def test_london_session_post_DST_summer_offset_open_at_8utc():
    """After spring-forward Mar 29: London is UTC+1 (BST). 08:00 UTC = 09:00
    BST → still inside the 08-16 local window."""
    _, status = _session_status("london", at=_utc(2026, 4, 1, 8))
    assert status == "OPEN"


def test_london_close_shifts_with_DST():
    """16:00 LOCAL is the close. Winter (Jan) → 16:00 UTC closes London.
    Summer (Jul) → London is UTC+1, so 15:00 UTC = 16:00 BST = boundary;
    16:00 UTC is already past close (17:00 BST)."""
    _, jan_close = _session_status("london", at=_utc(2026, 1, 15, 16))
    _, jul_close = _session_status("london", at=_utc(2026, 7, 15, 16))
    assert jan_close == "CLOSED"
    assert jul_close == "CLOSED"

    # And right before each close, both must be OPEN.
    _, jan_pre = _session_status("london", at=_utc(2026, 1, 15, 15, 30))
    _, jul_pre = _session_status("london", at=_utc(2026, 7, 15, 14, 30))
    assert jan_pre == "OPEN"
    assert jul_pre == "OPEN"


def test_ny_session_pre_and_post_DST():
    """NY is UTC-5 in winter, UTC-4 in summer. 13:00 UTC opens NY in
    winter (08:00 NY) but is too early in summer (09:00 NY is the open)."""
    _, winter_open = _session_status("newyork", at=_utc(2026, 1, 15, 13))
    _, summer_just_before = _session_status("newyork", at=_utc(2026, 7, 15, 11, 59))
    _, summer_open = _session_status("newyork", at=_utc(2026, 7, 15, 12))
    assert winter_open == "OPEN"
    assert summer_just_before == "CLOSED"
    assert summer_open == "OPEN"


def test_tokyo_has_no_DST_so_window_is_fixed():
    """Asia/Tokyo doesn't observe DST. Tokyo session is always 00-06 UTC."""
    _, winter = _session_status("tokyo", at=_utc(2026, 1, 15, 3))
    _, summer = _session_status("tokyo", at=_utc(2026, 7, 15, 3))
    assert winter == "OPEN"
    assert summer == "OPEN"
