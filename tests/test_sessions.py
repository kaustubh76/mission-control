from ictbot.runtime.sessions import get_sessions


def test_all_keys_present():
    s = get_sessions()
    expected = {
        "india_time",
        "tokyo_time",
        "tokyo_status",
        "london_time",
        "london_status",
        "newyork_time",
        "newyork_status",
        "active_session",
        "allow_trade",
    }
    assert expected.issubset(s.keys())


def test_status_values():
    s = get_sessions()
    for k in ("tokyo_status", "london_status", "newyork_status"):
        assert s[k] in ("OPEN", "CLOSED")


def test_times_are_hhmmss():
    s = get_sessions()
    for k in ("india_time", "tokyo_time", "london_time", "newyork_time"):
        parts = s[k].split(":")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)


def test_crypto_always_allowed():
    assert get_sessions()["allow_trade"] is True
