"""
Tests for the break-even trailing stop in backtest.run_backtest.

Strategy: build a synthetic 1m frame where price moves up by exactly 1R,
triggering the trail; then dips below the original SL but stays above
the new BE-SL. With trailing on, the outcome should be BE. Without
trailing on, the outcome should be LOSS.
"""

import unittest.mock as mock

import pandas as pd

from ictbot.engine import backtest as bt


def _series_ending_at(
    n, freq_min, end_ts, opens=None, highs=None, lows=None, closes=None, vols=None
):
    """Build n bars where each list provides per-bar values."""
    opens = opens or [100] * n
    highs = highs or [101] * n
    lows = lows or [99] * n
    closes = closes or [100] * n
    vols = vols or [10] * n
    return pd.DataFrame(
        [
            {
                "time": end_ts - pd.Timedelta(minutes=freq_min * (n - 1 - i)),
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
                "volume": vols[i],
            }
            for i in range(n)
        ]
    )


def _fake_eval():
    """Fires a BUY @ price=100, SL=99, TP=103 on the FIRST call only.
    Returns NO ENTRY on every subsequent call so the test position is the
    only one in the report."""
    state = {"fired": False}

    def fake(*args, **kwargs):
        base = {
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
            "entry": "NO ENTRY",
            "sl": 0.0,
            "tp": 0.0,
            "rr": 0.0,
            "confidence": 0,
            "diagnostics": {
                "buy_blockers": [],
                "sell_blockers": [],
                "closest_direction": "BUY",
                "blockers": [],
                "near_miss": False,
                "total_conditions": 5,
            },
        }
        if not state["fired"]:
            state["fired"] = True
            base.update({"entry": "BUY", "sl": 99.0, "tp": 103.0, "rr": 3.0, "confidence": 100})
        return base

    return fake


def _scenario():
    """
    Build a flat 'inert' price stream so the analyzer's first call sets up
    the BUY @ 100, then craft the last 2 bars to test the trail.

    Inert calm bars: high=100.5, low=99.5 (neither hits SL=99, nor +1R trail
    trigger at high>=101).

    Bar n-2: high=101.5 (hits +1R trail), low=100 (stays above new SL=100)
    Bar n-1: low=98 (would hit original SL=99 → LOSS without trail; but with
             trail SL is 100, low=98<100 → still a stop hit but be_moved=True
             so outcome=BE)
    """
    end = pd.Timestamp("2026-01-10 00:00")
    n = 80
    opens = [100] * n
    highs = [100.5] * n  # inert: doesn't reach trail trigger 101
    lows = [99.5] * n  # inert: doesn't hit SL=99
    closes = [100] * n
    # Bar n-2: 1R favorable move triggers trail-to-BE
    highs[n - 2] = 101.5  # > entry + 1R
    lows[n - 2] = 100  # stays above new BE-SL
    # Bar n-1: dips to 98 — original SL=99 would have been LOSS, new BE SL=100
    highs[n - 1] = 100.5
    lows[n - 1] = 98
    closes[n - 1] = 99
    return _series_ending_at(n, 1, end, opens=opens, highs=highs, lows=lows, closes=closes)


def _history_for(entry_df):
    end = pd.Timestamp("2026-01-10 00:00")
    return {
        "htf": _series_ending_at(60, 240, end),
        "bias": _series_ending_at(100, 15, end),
        "poi": _series_ending_at(200, 3, end),
        "entry": entry_df,
    }


def test_no_trail_results_in_loss():
    entry_df = _scenario()
    history = _history_for(entry_df)
    with mock.patch.object(bt, "evaluate_frames", _fake_eval()):
        report = bt.run_backtest(
            "TEST",
            bars=10,
            history=history,
            quiet=True,
            trail_breakeven_R=None,
            fee_per_side=0,
            slippage_per_side=0,
        )
    closed = [s for s in report["signals"] if s["outcome"] in ("WIN", "LOSS", "BE")]
    assert len(closed) >= 1
    assert closed[0]["outcome"] == "LOSS"


def test_trail_to_be_converts_loss_into_breakeven():
    entry_df = _scenario()
    history = _history_for(entry_df)
    with mock.patch.object(bt, "evaluate_frames", _fake_eval()):
        report = bt.run_backtest(
            "TEST",
            bars=10,
            history=history,
            quiet=True,
            trail_breakeven_R=1.0,
            fee_per_side=0,
            slippage_per_side=0,
        )
    closed = [s for s in report["signals"] if s["outcome"] in ("WIN", "LOSS", "BE")]
    assert len(closed) >= 1
    assert closed[0]["outcome"] == "BE"
    assert closed[0]["gross_R"] == 0.0
    assert closed[0]["be_moved"] is True


def test_trail_does_not_change_winners():
    """A clean WIN where price never retraces below entry stays a WIN
    even when trailing is on."""
    end = pd.Timestamp("2026-01-10 00:00")
    n = 80
    # Inert calm bars (same as _scenario)
    opens = [100] * n
    highs = [100.5] * n
    lows = [99.5] * n
    closes = [100] * n
    # Last bar: rip straight to TP without dipping below new BE-SL=100
    highs[n - 1] = 104  # ≥ TP=103 → WIN (also triggers trail-to-BE first)
    lows[n - 1] = 100.5  # > new BE-SL=100, so SL check doesn't fire
    entry_df = _series_ending_at(n, 1, end, opens=opens, highs=highs, lows=lows, closes=closes)
    history = _history_for(entry_df)
    with mock.patch.object(bt, "evaluate_frames", _fake_eval()):
        report = bt.run_backtest(
            "TEST",
            bars=10,
            history=history,
            quiet=True,
            trail_breakeven_R=1.0,
            fee_per_side=0,
            slippage_per_side=0,
        )
    closed = [s for s in report["signals"] if s["outcome"] in ("WIN", "LOSS", "BE")]
    assert closed[0]["outcome"] == "WIN"
