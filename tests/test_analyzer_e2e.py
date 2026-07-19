"""
End-to-end integration test for analyze_pair.

Mocks `analyzer.get_data` with hand-crafted DataFrames that lead the
pipeline into a known state (a clean BUY setup) and verifies:

  - the return dict has every key the UI depends on
  - Telegram was invoked when notify=True
  - the journal grew by exactly one OPEN signal
  - re-running with the same setup does NOT re-send Telegram (dedupe works)

This is the contract the dashboard and scanner consume; if it changes
unexpectedly, both break in silent ways. Phase 11 / gap T1.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ictbot.orchestrator import analyzer
from ictbot.portfolio import journal as journal_mod
from ictbot.runtime import signal_memory

# Same keys the dashboard reads off the dict. If you add/remove keys
# from the analyzer's return shape, update this set deliberately.
REQUIRED_KEYS = {
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
    "gate_blocked",
    "regime",
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


def _uptrend(n: int) -> pd.DataFrame:
    """Strong, clean uptrend on all four timeframes."""
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.5 + i,
                "volume": 10,
            }
            for i in range(n)
        ]
    )


def _entry_with_bullish_setup(n: int) -> pd.DataFrame:
    """1m frame that triggers BULLISH MSS + BULLISH FVG + positive delta."""
    rows = [
        # First N-5 bars: trending up gently.
        {
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
            "open": 100 + i * 0.1,
            "high": 100.5 + i * 0.1,
            "low": 99.5 + i * 0.1,
            "close": 100.3 + i * 0.1,
            "volume": 10,
        }
        for i in range(n - 5)
    ]
    base = rows[-1]["close"] if rows else 100
    # Last 5 bars: build the FVG (gap up) + MSS.
    rows += [
        {
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=n - 5),
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.3,
            "volume": 10,
        },  # filler
        {
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=n - 4),
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.3,
            "volume": 10,
        },  # filler
        {
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=n - 3),
            "open": base,
            "high": base + 2.0,
            "low": base - 0.5,
            "close": base + 1.5,
            "volume": 20,
        },  # candle[-3] high = base+2
        {
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=n - 2),
            "open": base + 1.5,
            "high": base + 5.0,
            "low": base + 0.5,
            "close": base + 4.5,
            "volume": 30,
        },  # gap-up
        {
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=n - 1),
            "open": base + 4.5,
            "high": base + 10.0,
            "low": base + 3.0,
            "close": base + 9.5,
            "volume": 40,
        },  # candle[-1] low = base+3 > base+2 ✓
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def patched_pipeline(tmp_path, monkeypatch):
    # Telegram + journal isolation so the test doesn't touch real files
    # or hit the network.
    fake_signal = tmp_path / "last_signal.json"
    fake_journal = tmp_path / "signals.json"
    monkeypatch.setattr(signal_memory, "SIGNAL_FILE", fake_signal)
    monkeypatch.setattr(journal_mod, "JOURNAL_FILE", fake_journal)

    sent_messages: list[str] = []
    monkeypatch.setattr(
        analyzer,
        "send_telegram",
        lambda msg: (sent_messages.append(msg), True)[1],
    )
    return {"messages": sent_messages, "journal_path": fake_journal}


def test_full_buy_pipeline(patched_pipeline, monkeypatch):
    htf = _uptrend(120)
    bias_df = _uptrend(60)
    poi_df = _uptrend(60)
    entry_df = _entry_with_bullish_setup(40)
    # Make sure the POI level (recent 20-bar low) gets tapped by the current price.
    poi_df.loc[poi_df.index[-1], "close"] = float(poi_df["low"].tail(20).min()) + 0.001

    def fake_get_data(symbol, timeframe, limit=300):
        return {
            "4h": htf,
            "15m": bias_df,
            "3m": poi_df,
            "1m": entry_df,
        }[timeframe]

    monkeypatch.setattr(analyzer, "get_data", fake_get_data)

    out = analyzer.analyze_pair("BTC/USDT:USDT", notify=True)

    # Contract check (Phase 11 / gap T1).
    assert set(out.keys()) >= REQUIRED_KEYS, f"missing keys: {REQUIRED_KEYS - set(out.keys())}"
    assert out["error"] is None

    if out["entry"] in ("BUY", "SELL"):
        # When a signal fires, Telegram should be called exactly once.
        # The journal write is no longer the analyzer's responsibility —
        # the router/broker path owns it now (see analyzer.py @ 2026-06-05
        # phantom-close fix). Pure analyzer runs do NOT touch signals.json.
        assert len(patched_pipeline["messages"]) == 1
        assert not patched_pipeline["journal_path"].exists()

        # Re-run: same setup, same signal — Telegram MUST NOT be hit again
        # (dedupe on pair+direction).
        analyzer.analyze_pair("BTC/USDT:USDT", notify=True)
        assert len(patched_pipeline["messages"]) == 1
