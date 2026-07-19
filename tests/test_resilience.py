"""
Resilience tests — analyzer must never crash on empty/short/bad data.
The UI and scanner depend on _empty_result keeping all keys present.
"""

import pandas as pd
import pytest

from ictbot.orchestrator import analyzer
from ictbot.orchestrator.analyzer import _empty_result, evaluate_frames


@pytest.fixture
def fake_session():
    return {
        "india_time": "00:00:00",
        "tokyo_time": "00:00:00",
        "tokyo_status": "CLOSED",
        "london_time": "00:00:00",
        "london_status": "CLOSED",
        "newyork_time": "00:00:00",
        "newyork_status": "CLOSED",
        "active_session": "OFF HOURS (24H CRYPTO)",
        "allow_trade": True,
    }


def _df(n, o=100, h=101, l=99, c=100, v=10):
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
            for i in range(n)
        ]
    )


def test_empty_frame_returns_error_not_exception(fake_session):
    r = evaluate_frames(_df(0), _df(0), _df(0), _df(0), fake_session, "X")
    assert r["error"] is not None
    assert r["entry"] == "NO ENTRY"


def test_short_htf_returns_error(fake_session):
    r = evaluate_frames(_df(10), _df(100), _df(100), _df(100), fake_session, "X")
    assert "htf" in r["error"]


def test_short_entry_returns_error(fake_session):
    r = evaluate_frames(_df(100), _df(100), _df(100), _df(2), fake_session, "X")
    assert "entry" in r["error"]


def test_empty_result_has_all_required_keys(fake_session):
    """UI must not KeyError when data is missing."""
    r = _empty_result("X", fake_session, error="boom")
    # The same keys app.py reads in the happy path
    needed = {
        "pair",
        "error",
        "price",
        "last_close",
        "htf_bias",
        "ltf_bias",
        "ltf_poi",
        "poi_tap",
        "ltf_mss",
        "fvg",
        "micro_fvg",
        "delta",
        "atr_1m",
        "entry",
        "sl",
        "tp",
        "rr",
        "confidence",
        "diagnostics",
        "india_time",
        "tokyo_time",
        "tokyo_status",
        "london_time",
        "london_status",
        "newyork_time",
        "newyork_status",
        "active_session",
        "ltf_df",
        "poi_df",
    }
    missing = needed - set(r.keys())
    assert not missing, f"missing keys: {missing}"


def test_fetch_exception_returns_error(monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(analyzer, "get_data", boom)
    r = analyzer.analyze_pair("BTC/USDT:USDT", notify=False)
    assert r["error"] and "network down" in r["error"]
    assert r["entry"] == "NO ENTRY"


def test_none_frame_returns_error(fake_session):
    r = evaluate_frames(None, _df(100), _df(100), _df(100), fake_session, "X")
    assert r["error"] is not None
