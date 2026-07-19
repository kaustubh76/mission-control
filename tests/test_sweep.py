"""
Tests for sweep.py (parameter grid search).
Uses mocked backtest results so we don't hit the network or do real work.
"""

from ictbot.engine import sweep


def _fake_report(signals_outcomes, rr=3.0):
    """Build a fake backtest report from a list of WIN/LOSS/OPEN strings.

    Populates net_R per-trade because the new _score aggregates that directly
    (instead of inferring it from a global rr × win_count).
    """
    sigs = []
    for o in signals_outcomes:
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


def test_score_handles_zero_closed_signals():
    r = _fake_report([])
    s = sweep._score(r)
    assert s["wins"] == 0 and s["losses"] == 0
    assert s["win_rate"] is None
    assert s["expectancy_R"] is None


def test_score_computes_win_rate():
    r = _fake_report(["WIN", "WIN", "LOSS", "LOSS", "OPEN"])
    s = sweep._score(r)
    assert s["wins"] == 2
    assert s["losses"] == 2
    assert s["open"] == 1
    assert s["win_rate"] == 50.0


def test_score_computes_expectancy_in_R():
    # 2 wins at 3R, 2 losses at -1R, closed=4
    # expectancy = (2*3 + 2*-1) / 4 = (6 - 2)/4 = 1.0R
    r = _fake_report(["WIN", "WIN", "LOSS", "LOSS"], rr=3.0)
    s = sweep._score(r)
    assert s["expectancy_R"] == 1.0


def test_run_sweep_iterates_grid(monkeypatch):
    calls = []

    def fake_run_backtest(
        pair, bars, verbose=False, *, poi_tolerance, sl_frac, tp_frac, require_fvg, quiet=False
    ):
        calls.append((poi_tolerance, sl_frac, tp_frac, require_fvg))
        return _fake_report(["WIN"])

    monkeypatch.setattr(sweep, "run_backtest", fake_run_backtest)

    grid = {
        "poi_tol": [0.001, 0.002],
        "sl": [0.005],
        "tp": [0.015],
        "require_fvg": [True, False],
    }
    results = sweep.run_sweep("BTC/USDT:USDT", 100, grid)
    assert len(calls) == 4  # 2 * 1 * 1 * 2
    assert len(results) == 4
    assert all("win_rate" in r for r in results)


def test_print_top_handles_no_closed_combos(monkeypatch, capsys):
    # All combos with 0 closed trades — should print a graceful note
    sweep.print_top(
        [
            {
                "poi_tol": 0.001,
                "sl": 0.005,
                "tp": 0.015,
                "require_fvg": True,
                "signals": 0,
                "wins": 0,
                "losses": 0,
                "open": 0,
                "win_rate": None,
                "expectancy_R": None,
                "dt": 0.1,
            },
        ]
    )
    out = capsys.readouterr().out
    assert "No combo" in out
