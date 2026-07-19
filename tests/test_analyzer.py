"""
Integration tests for analyzer.analyze_pair.

Mocks core.exchange.get_data so we can drive the analyzer into specific
states (full BUY setup, full SELL setup, no-setup) and verify the return
contract that app.py and scanner.py depend on.
"""

import pandas as pd
import pytest

from ictbot.orchestrator import analyzer

# Every key app.py / scanner.py reads off the return dict.
REQUIRED_KEYS = {
    "pair",
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


def _series(open_, high, low, close, vol, n=300):
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": vol,
            }
            for i in range(n)
        ]
    )


@pytest.fixture
def patch_data(monkeypatch):
    """Helper that lets each test inject its own per-timeframe frame."""

    def _apply(htf, bias, poi, entry):
        frames = {"4h": htf, "15m": bias, "3m": poi, "1m": entry}

        def fake_get_data(symbol, timeframe, limit=300):
            return frames[timeframe].copy()

        monkeypatch.setattr(analyzer, "get_data", fake_get_data)
        # Make sure no real Telegram call ever happens
        monkeypatch.setattr(analyzer, "send_telegram", lambda *_a, **_k: True)

    return _apply


def test_no_entry_on_flat_market(patch_data):
    flat = _series(100, 101, 99, 100, 10)
    patch_data(flat, flat, flat, flat)
    r = analyzer.analyze_pair("BTC/USDT:USDT", notify=False)
    assert r["entry"] == "NO ENTRY"
    assert r["sl"] == 0 and r["tp"] == 0


def test_return_dict_has_all_keys(patch_data):
    flat = _series(100, 101, 99, 100, 10)
    patch_data(flat, flat, flat, flat)
    r = analyzer.analyze_pair("BTC/USDT:USDT", notify=False)
    missing = REQUIRED_KEYS - set(r.keys())
    assert not missing, f"missing keys: {missing}"


def test_full_buy_setup_emits_BUY(patch_data, monkeypatch):
    """All six conditions aligned bullish → BUY."""
    # HTF + LTF both BULLISH → strong uptrend
    up = pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.5 + i,
                "volume": 10,
            }
            for i in range(300)
        ]
    )

    # 3m POI frame: lowest low = 100 in last 20 candles, current close at 100.1
    poi_rows = [
        {
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
            "open": 100,
            "high": 101,
            "low": 99 + i,
            "close": 100,
            "volume": 10,
        }
        for i in range(300)
    ]
    # Force last close near the recent-low POI so poi_tap fires
    poi_df = pd.DataFrame(poi_rows)
    recent_low = poi_df["low"].tail(20).min()
    poi_df.loc[poi_df.index[-1], "close"] = recent_low  # exact tap

    # 1m entry frame: bullish bias + higher high MSS + bullish FVG + positive delta
    # Build last 5 rows to satisfy FVG (low[-1] > high[-3]) and MSS (last high > prev high)
    base = [
        {
            "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 101,
            "volume": 10,
        }
        for i in range(295)
    ]
    entry_df = pd.DataFrame(
        base
        + [
            {
                "time": pd.Timestamp("2026-01-01 04:55"),
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 10,
            },
            {
                "time": pd.Timestamp("2026-01-01 04:56"),
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 10,
            },
            {
                "time": pd.Timestamp("2026-01-01 04:57"),
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 10,
            },  # candle[-3].high=102
            {
                "time": pd.Timestamp("2026-01-01 04:58"),
                "open": 102,
                "high": 105,
                "low": 102,
                "close": 104,
                "volume": 10,
            },  # gap-up
            {
                "time": pd.Timestamp("2026-01-01 04:59"),
                "open": 105,
                "high": 110,
                "low": 103,
                "close": 108,
                "volume": 10,
            },  # candle[-1].low=103>102 ✓, high>prev_high ✓
        ]
    )

    patch_data(up, up, poi_df, entry_df)
    # invert=False bypasses STRATEGY_MODE so we test the raw analyzer mechanics.
    # mss_mode="simple" because the hand-crafted fixture is built to satisfy
    # the legacy 2-bar rule, not the swing-protection rule (E2 default).
    # mss_timeframe="entry" preserves the pre-Phase-B behaviour this
    # fixture was authored against — the entry_df has the MSS-triggering
    # structure, not poi_df.
    r = analyzer.analyze_pair(
        "BTC/USDT:USDT",
        notify=False,
        invert=False,
        mss_mode="simple",
        mss_timeframe="entry",
        # Phase C: the fixture has FVG and MSS on the same iloc[-1] bar.
        # The canonical "strictly after MSS" gate rejects that by design;
        # opt out to preserve this test's pre-Phase-C assumption.
        require_fvg_after_mss=False,
        # Phase D: no retest can exist in a fixture this small; opt out
        # so the test's pre-Phase-D BUY assertion survives.
        require_mfvg_retest=False,
    )

    assert r["htf_bias"] == "BULLISH"
    assert r["poi_tap"] == "POI TAPPED"
    assert r["ltf_mss"] == "BULLISH MSS"
    assert r["fvg"] == "BULLISH FVG"
    assert r["delta"] > 0
    assert r["entry"] == "BUY"
    assert r["sl"] < r["price"] < r["tp"]
    assert r["confidence"] == 100


def test_notify_false_never_calls_telegram(patch_data, monkeypatch):
    flat = _series(100, 101, 99, 100, 10)
    patch_data(flat, flat, flat, flat)
    called = []
    monkeypatch.setattr(analyzer, "send_telegram", lambda msg: called.append(msg) or True)
    analyzer.analyze_pair("BTC/USDT:USDT", notify=False)
    assert called == []
