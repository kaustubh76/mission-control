"""
Tests for wfo.run_wfo. Uses mocked backtest results so we don't hit the
network or do real work — just verify the train/test logic and reporting.
"""

import pandas as pd

from ictbot.engine import wfo


def _frames():
    """A 1000-bar 1m frame plus dummy HTF/15m/3m frames."""
    return {
        "htf": pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=100, freq="4h"),
                "open": [100] * 100,
                "high": [101] * 100,
                "low": [99] * 100,
                "close": [100] * 100,
                "volume": [1] * 100,
            }
        ),
        "bias": pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=300, freq="15min"),
                "open": [100] * 300,
                "high": [101] * 300,
                "low": [99] * 300,
                "close": [100] * 300,
                "volume": [1] * 300,
            }
        ),
        "poi": pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=400, freq="3min"),
                "open": [100] * 400,
                "high": [101] * 400,
                "low": [99] * 400,
                "close": [100] * 400,
                "volume": [1] * 400,
            }
        ),
        "entry": pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=1000, freq="1min"),
                "open": [100] * 1000,
                "high": [101] * 1000,
                "low": [99] * 1000,
                "close": [100] * 1000,
                "volume": [1] * 1000,
            }
        ),
    }


def _fake_report(outcomes, rr=3.0):
    sigs = []
    for o in outcomes:
        net = rr if o == "WIN" else (-1 if o == "LOSS" else 0)
        sigs.append(
            {
                "outcome": o,
                "rr": rr,
                "entry": "BUY",
                "price": 100,
                "sl": 99,
                "tp": 103,
                "confidence": 100,
                "gross_R": net,
                "friction_R": 0.0,
                "net_R": net,
            }
        )
    return {
        "pair": "TEST",
        "bars_scanned": 100,
        "counts": {},
        "signals": sigs,
        "near_misses": [],
        "verbose": False,
    }


def test_run_wfo_picks_train_winner_and_reports_test(monkeypatch):
    # Fake fetch returns synthetic frames
    monkeypatch.setattr(wfo, "fetch_history", lambda pair, bars: _frames())

    # Fake backtest: TRAIN gives different outcomes per config; TEST always 1W 1L
    # Outcomes designed so winning config has >= 3 CLOSED trades.
    train_outcomes_by_config = {
        (0.001, 0.005, 0.015, True): ["WIN", "WIN", "WIN", "LOSS"],  # 4 closed, exp = 2.0R
        (0.001, 0.005, 0.015, False): ["WIN", "WIN", "LOSS", "LOSS"],  # 4 closed, exp = 1.0R
    }
    test_outcomes = ["WIN", "LOSS"]  # exp = (3-1)/2 = 1.0R

    def fake_run(pair, history, start, end, *, poi_tol, sl, tp, fvg, invert=False, **_):
        key = (poi_tol, sl, tp, fvg)
        train_range_end = start + (end - start)  # any range; we only care about params
        # Heuristic: small window = TEST (because we patch with train_frac=0.5)
        # Actually just use the SECOND call to differentiate TRAIN vs TEST per config.
        # Simpler: track call order.
        if key in train_outcomes_by_config and fake_run.call_count[key] == 0:
            fake_run.call_count[key] += 1
            return _fake_report(train_outcomes_by_config[key])
        return _fake_report(test_outcomes)

    fake_run.call_count = {k: 0 for k in train_outcomes_by_config}
    monkeypatch.setattr(wfo, "_run_one", fake_run)

    grid = {"poi_tol": [0.001], "sl": [0.005], "tp": [0.015], "require_fvg": [True, False]}
    out = wfo.run_wfo("BTC/USDT:USDT", bars=500, train_frac=0.5, grid=grid)

    assert out["error"] is None
    assert out["winner"]["require_fvg"] is True  # 2.0R beat 0R on train
    assert out["test_score"]["wins"] == 1
    assert out["test_score"]["losses"] == 1


def test_run_wfo_no_winner_when_no_closed_trades(monkeypatch):
    monkeypatch.setattr(wfo, "fetch_history", lambda pair, bars: _frames())
    # All OPEN signals → not eligible
    monkeypatch.setattr(wfo, "_run_one", lambda *a, **kw: _fake_report(["OPEN", "OPEN"]))

    grid = {"poi_tol": [0.001], "sl": [0.005], "tp": [0.015], "require_fvg": [True]}
    out = wfo.run_wfo("BTC/USDT:USDT", bars=500, train_frac=0.5, grid=grid)
    assert out["winner"] is None
    assert "No config" in out["error"]


def test_print_report_handles_winner(monkeypatch, capsys):
    out = {
        "pair": "BTC/USDT:USDT",
        "error": None,
        "winner": {
            "poi_tol": 0.003,
            "sl": 0.005,
            "tp": 0.015,
            "require_fvg": True,
            "signals": 5,
            "wins": 4,
            "losses": 1,
            "open": 0,
            "win_rate": 80.0,
            "expectancy_R": 2.2,
        },
        "test_score": {
            "signals": 3,
            "wins": 2,
            "losses": 1,
            "open": 0,
            "win_rate": 66.7,
            "expectancy_R": 1.33,
        },
    }
    wfo.print_report(out)
    captured = capsys.readouterr().out
    assert "WALK-FORWARD REPORT" in captured
    assert "BTC/USDT:USDT" in captured
    assert "Edge holds" in captured or "VERDICT" in captured


def test_print_report_flags_overfit(monkeypatch, capsys):
    out = {
        "pair": "BTC/USDT:USDT",
        "error": None,
        "winner": {
            "poi_tol": 0.003,
            "sl": 0.005,
            "tp": 0.015,
            "require_fvg": True,
            "signals": 5,
            "wins": 4,
            "losses": 1,
            "open": 0,
            "win_rate": 80.0,
            "expectancy_R": 2.2,
        },
        "test_score": {
            "signals": 3,
            "wins": 0,
            "losses": 3,
            "open": 0,
            "win_rate": 0.0,
            "expectancy_R": -1.0,
        },
    }
    wfo.print_report(out)
    captured = capsys.readouterr().out
    assert "overfit" in captured.lower()
