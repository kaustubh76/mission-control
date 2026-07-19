"""
Test the multi-TF backtest using mocked OHLCV — no network.

The fixtures align all timeframes' end-time to the same wall-clock
moment so that the walk-forward slicer always has enough history on
every TF.
"""

import pandas as pd
import pytest

from ictbot.engine import backtest

END = pd.Timestamp("2026-01-15 00:00")


def _series_ending_at(n, freq_min, end_ts=END, o=100, h=101, l=99, c=100, v=10):
    """Build `n` bars of `freq_min`-minute candles ending at end_ts."""
    return pd.DataFrame(
        [
            {
                "time": end_ts - pd.Timedelta(minutes=freq_min * (n - 1 - i)),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
            for i in range(n)
        ]
    )


@pytest.fixture
def patch_get_data(monkeypatch):
    def _set(df_by_tf):
        def fake(symbol, tf, limit=300):
            return df_by_tf[tf].copy().tail(limit).reset_index(drop=True)

        monkeypatch.setattr(backtest, "get_data", fake)

    return _set


@pytest.fixture
def flat_frames():
    return {
        "4h": _series_ending_at(100, 240),
        "15m": _series_ending_at(300, 15),
        "3m": _series_ending_at(600, 3),
        "1m": _series_ending_at(600, 1),
    }


def test_backtest_flat_market_yields_no_signals(patch_get_data, flat_frames):
    patch_get_data(flat_frames)
    report = backtest.run_backtest("BTC/USDT:USDT", bars=100, verbose=False)
    assert report["bars_scanned"] > 0
    assert report["counts"].get("BUY", 0) == 0
    assert report["counts"].get("SELL", 0) == 0
    # Either NO ENTRY or all conditions met somewhere — but never a signal
    no_signal_count = report["counts"].get("NO ENTRY", 0) + report["counts"].get(
        "INSUFFICIENT DATA", 0
    )
    assert no_signal_count > 0


def test_backtest_report_shape(patch_get_data, flat_frames):
    patch_get_data(flat_frames)
    report = backtest.run_backtest("BTC/USDT:USDT", bars=50, verbose=True)
    assert set(report.keys()) >= {
        "pair",
        "bars_scanned",
        "counts",
        "signals",
        "near_misses",
        "verbose",
    }
    assert isinstance(report["signals"], list)
    assert isinstance(report["near_misses"], list)


def test_score_signals_marks_win_loss_open():
    # Bar 0: signal time. Bar 1: hits TP without touching SL. Bar 2: hits SL.
    entry_full = pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01 00:00"),
                "open": 100,
                "high": 100,
                "low": 100,
                "close": 100,
                "volume": 1,
            },
            {
                "time": pd.Timestamp("2026-01-01 00:01"),
                "open": 100,
                "high": 104,
                "low": 100,
                "close": 103,
                "volume": 1,
            },  # WIN: high=104>=tp=103, low=100>sl=99
            {
                "time": pd.Timestamp("2026-01-01 00:02"),
                "open": 100,
                "high": 100,
                "low": 96,
                "close": 97,
                "volume": 1,
            },  # LOSS: low=96<sl=99
        ]
    )
    signals = [
        # Signal at 00:00; next bar wins
        {
            "time": pd.Timestamp("2026-01-01 00:00"),
            "entry": "BUY",
            "price": 100,
            "sl": 99,
            "tp": 103,
            "rr": 3.0,
            "confidence": 100,
            "i": 0,
            "htf_bias": "BULLISH",
            "ltf_bias": "BULLISH",
        },
        # Signal at 00:01; next bar's low=96 hits SL=99
        {
            "time": pd.Timestamp("2026-01-01 00:01"),
            "entry": "BUY",
            "price": 103,
            "sl": 99,
            "tp": 110,
            "rr": 3.0,
            "confidence": 100,
            "i": 1,
            "htf_bias": "BULLISH",
            "ltf_bias": "BULLISH",
        },
        # Signal at 00:02; no future bars
        {
            "time": pd.Timestamp("2026-01-01 00:02"),
            "entry": "BUY",
            "price": 97,
            "sl": 95,
            "tp": 105,
            "rr": 3.0,
            "confidence": 100,
            "i": 2,
            "htf_bias": "BULLISH",
            "ltf_bias": "BULLISH",
        },
    ]
    scored = backtest._score_signals(signals, entry_full)
    assert scored[0]["outcome"] == "WIN"
    assert scored[1]["outcome"] == "LOSS"
    assert scored[2]["outcome"] == "OPEN"


def test_position_aware_loop_blocks_new_entries_while_in_position():
    """Once a position is open, the backtest must not fire new signals
    until SL or TP closes it."""
    # Build entry frame where the analyzer would fire a signal at bar 50.
    # While that position is open, we shouldn't see more signals on bars 51-99.
    from ictbot.engine import backtest as bt

    history = {
        "htf": _series_ending_at(60, 240),
        "bias": _series_ending_at(100, 15),
        "poi": _series_ending_at(200, 3),
        "entry": _series_ending_at(200, 1),
    }

    # Fake evaluate: BUY at price 100 with SL=50, TP=200 — neither will hit
    # in the fake price action (which stays at 99-101) so the position stays open
    # for the rest of the backtest and no new entries should fire.
    def fake_evaluate(*args, **kwargs):
        return {
            "pair": "TEST",
            "error": None,
            "price": 100.0,
            "last_close": 100.0,
            "htf_bias": "BULLISH",
            "ltf_bias": "BULLISH",
            "ltf_poi": 99.0,
            "poi_tap": "POI TAPPED",
            "ltf_mss": "BULLISH MSS",
            "fvg": "BULLISH FVG",
            "micro_fvg": "BULLISH FVG",
            "delta": 100,
            "atr_1m": 1.0,
            "entry": "BUY",
            "sl": 50.0,
            "tp": 200.0,
            "rr": 2.0,
            "confidence": 100,
            "diagnostics": {
                "buy_blockers": [],
                "sell_blockers": [],
                "closest_direction": "BUY",
                "blockers": [],
                "near_miss": False,
                "total_conditions": 5,
            },
        }

    import unittest.mock as mock

    with mock.patch.object(bt, "evaluate_frames", fake_evaluate):
        report = bt.run_backtest("TEST", bars=100, history=history, quiet=True)

    # Only one signal should be in the report (the rest of the bars were
    # IN POSITION rather than firing new evaluations).
    assert len(report["signals"]) == 1
    # IN POSITION counter should be > 0
    assert report["counts"].get("IN POSITION", 0) > 0


def test_print_report_does_not_crash(patch_get_data, flat_frames, capsys):
    patch_get_data(flat_frames)
    report = backtest.run_backtest("BTC/USDT:USDT", bars=20, verbose=True)
    backtest.print_report(report)
    out = capsys.readouterr().out
    assert "BACKTEST REPORT" in out
    assert "BTC/USDT:USDT" in out
