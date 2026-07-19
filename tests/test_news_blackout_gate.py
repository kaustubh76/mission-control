"""Integration test: news_blackout_minutes gate inside ICTProMaxStrategy.

We don't hit the live FF feed — `runtime.news.is_blackout` is monkey-patched
to return a synthetic event. The point of these tests is to prove the gate
plumbing is wired through the strategy and into the result dict.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from ictbot.data.forex_factory import NewsEvent
from ictbot.runtime import news as runtime_news
from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _bars(n: int, base: float = 100.0) -> pd.DataFrame:
    """Minimal OHLCV DataFrame with monotonic time + bullish drift."""
    times = pd.date_range("2026-05-01", periods=n, freq="1min")
    closes = [base + i * 0.01 for i in range(n)]
    return pd.DataFrame(
        {
            "time": times,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1.0] * n,
        }
    )


def _session() -> dict:
    return {
        "india_time": "—",
        "tokyo_time": "—",
        "tokyo_status": "—",
        "london_time": "—",
        "london_status": "—",
        "newyork_time": "—",
        "newyork_status": "—",
        "active_session": "—",
        "killzone_active": True,
        "allow_trade": True,
    }


def _frames():
    return _bars(60), _bars(40), _bars(40), _bars(20), _session()


# -----------------------------------------------------------------------------


def test_gate_off_by_default(monkeypatch):
    """With news_blackout_minutes=0 the gate must NOT call the feed."""
    called = {"hit": False}

    def boom(*a, **kw):
        called["hit"] = True
        raise AssertionError("news feed must not be touched when gate is off")

    monkeypatch.setattr(runtime_news, "is_blackout", boom)

    strat = ICTProMaxStrategy()  # default news_blackout_minutes=0
    htf, bias, poi, entry, sess = _frames()
    out = strat.evaluate(htf, bias, poi, entry, sess, pair="TEST")
    assert called["hit"] is False
    assert out["news_event"] is None
    # Gate didn't reject for news reasons
    assert (out["gate_blocked"] or "").find("news") == -1


def test_gate_blocks_when_event_in_window(monkeypatch):
    """Event within ±N min → gate_blocked must mention 'news', entry NO ENTRY."""
    fake_event = NewsEvent(
        title="US Core PCE",
        country="USD",
        impact="High",
        ts=datetime.now(timezone.utc) + timedelta(minutes=8),
        forecast="0.3%",
        previous="0.3%",
        url="",
    )
    monkeypatch.setattr(runtime_news, "is_blackout", lambda *a, **kw: fake_event)

    strat = ICTProMaxStrategy(news_blackout_minutes=15.0)
    htf, bias, poi, entry, sess = _frames()
    out = strat.evaluate(htf, bias, poi, entry, sess, pair="TEST")

    assert out["entry"] == "NO ENTRY"
    assert out["gate_blocked"] is not None
    assert "news blackout" in out["gate_blocked"]
    assert "US Core PCE" in out["gate_blocked"]
    assert out["news_event"] is not None
    assert out["news_event"]["title"] == "US Core PCE"
    assert out["news_event"]["country"] == "USD"


def test_gate_clear_when_no_event(monkeypatch):
    """is_blackout returns None → strategy should be free to fire normally."""
    monkeypatch.setattr(runtime_news, "is_blackout", lambda *a, **kw: None)

    strat = ICTProMaxStrategy(news_blackout_minutes=15.0)
    htf, bias, poi, entry, sess = _frames()
    out = strat.evaluate(htf, bias, poi, entry, sess, pair="TEST")
    # Gate must not insert a 'news' reason when feed says clear.
    assert "news" not in (out["gate_blocked"] or "")
    assert out["news_event"] is None


def test_feed_outage_fails_safe(monkeypatch):
    """If the feed raises and there's no cache, the gate must BLOCK (not allow).

    A trade fired during a feed outage right next to CPI is the failure mode
    we're explicitly defending against.
    """

    def boom(*a, **kw):
        raise RuntimeError("FF mirror 503")

    monkeypatch.setattr(runtime_news, "is_blackout", boom)

    strat = ICTProMaxStrategy(news_blackout_minutes=15.0)
    htf, bias, poi, entry, sess = _frames()
    out = strat.evaluate(htf, bias, poi, entry, sess, pair="TEST")

    assert out["gate_blocked"] is not None
    assert "news feed unavailable" in out["gate_blocked"]
    assert out["entry"] == "NO ENTRY"
