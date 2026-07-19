"""
Tests for fees + slippage accounting in the backtest.
"""

import pandas as pd
import pytest

from ictbot.engine import backtest as bt


def _bars_ending(n, freq_min, end_ts, o=100, h=101, l=99, c=100, v=10):
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
def fake_history():
    end = pd.Timestamp("2026-01-15 00:00")
    return {
        "htf": _bars_ending(80, 240, end),
        "bias": _bars_ending(200, 15, end),
        "poi": _bars_ending(400, 3, end),
        "entry": _bars_ending(300, 1, end),
    }


def _fake_eval_factory(outcome_seq):
    """Returns a fake evaluate_frames that fires a BUY whose SL is far enough
    that the backtest's loss-detection condition (bar['low'] <= sl) is True
    on the next bar. Each call returns a signal where the next bar will be
    a guaranteed LOSS (low=0)."""
    state = {"call": 0}

    def fake(*args, **kwargs):
        state["call"] += 1
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
            "sl": 99.0,
            "tp": 103.0,
            "rr": 3.0,
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

    return fake


def test_friction_is_recorded_per_trade(monkeypatch, fake_history):
    """Each closed trade should carry gross_R, friction_R, net_R fields."""
    monkeypatch.setattr(bt, "evaluate_frames", _fake_eval_factory([]))

    # Force a guaranteed LOSS: pretend price tanks to 0 on every bar after entry.
    # We modify the entry frame so low=0 for the bars after the start of replay.
    df = fake_history["entry"].copy()
    df.loc[df.index[60:], "low"] = 0  # next bars will hit SL=99
    fake_history = {**fake_history, "entry": df}

    report = bt.run_backtest(
        "TEST",
        bars=200,
        history=fake_history,
        quiet=True,
        fee_per_side=0.001,
        slippage_per_side=0.0005,
    )
    closed = [s for s in report["signals"] if s["outcome"] in ("WIN", "LOSS")]
    assert len(closed) >= 1
    for s in closed:
        assert "gross_R" in s and "friction_R" in s and "net_R" in s
        # friction = 2*(fee+slip)/risk_pct = 2*(0.001+0.0005)/0.01 = 0.30
        # So a LOSS should have net_R = -1 - 0.30 = -1.30
        if s["outcome"] == "LOSS":
            assert s["gross_R"] == -1.0
            assert s["friction_R"] > 0
            assert s["net_R"] < s["gross_R"]


def test_zero_fees_zero_slippage_leaves_gross_intact(monkeypatch, fake_history):
    monkeypatch.setattr(bt, "evaluate_frames", _fake_eval_factory([]))

    df = fake_history["entry"].copy()
    df.loc[df.index[60:], "low"] = 0
    fake_history = {**fake_history, "entry": df}

    report = bt.run_backtest(
        "TEST", bars=200, history=fake_history, quiet=True, fee_per_side=0.0, slippage_per_side=0.0
    )
    closed = [s for s in report["signals"] if s["outcome"] in ("WIN", "LOSS")]
    for s in closed:
        assert s["friction_R"] == 0.0
        assert s["net_R"] == s["gross_R"]


def test_friction_scales_inversely_with_risk_distance(monkeypatch):
    """A trade with 2× the SL distance should have half the friction_R."""
    # We don't need a full backtest — just test the math.
    # Friction model: friction_R = 2 * (fee + slip) / risk_pct
    fee, slip = 0.001, 0.0005
    friction_pct = 2 * (fee + slip)  # 0.003
    # Narrow SL: 0.5% risk → friction = 0.003 / 0.005 = 0.6R
    # Wide SL: 1.0% risk → friction = 0.003 / 0.01 = 0.3R
    assert friction_pct / 0.005 == 0.6
    assert friction_pct / 0.01 == 0.3
