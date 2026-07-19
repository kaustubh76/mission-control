"""
Tests for wfo.run_all_pairs + print_scoreboard.
"""

from ictbot.engine import wfo


def _fake_wfo_result(pair, verdict="holds"):
    if verdict == "no_winner":
        return {"pair": pair, "error": None, "winner": None, "test_score": None}
    if verdict == "holds":
        # F3 small-sample gate (ROADMAP §F3): TEST closures must be ≥ 10
        # for "✅ holds" — otherwise verdict becomes "small sample".
        return {
            "pair": pair,
            "error": None,
            "winner": {
                "poi_tol": 0.005,
                "sl": 0.003,
                "tp": 0.009,
                "require_fvg": False,
                "signals": 25,
                "wins": 15,
                "losses": 10,
                "open": 0,
                "win_rate": 60.0,
                "expectancy_R": 1.5,
            },
            "test_score": {
                "signals": 15,
                "wins": 10,
                "losses": 5,
                "open": 0,
                "win_rate": 66.7,
                "expectancy_R": 0.8,
            },
        }
    # overfit
    return {
        "pair": pair,
        "error": None,
        "winner": {
            "poi_tol": 0.005,
            "sl": 0.003,
            "tp": 0.009,
            "require_fvg": False,
            "signals": 5,
            "wins": 4,
            "losses": 1,
            "open": 0,
            "win_rate": 80.0,
            "expectancy_R": 2.4,
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


def test_run_all_pairs_iterates_every_pair(monkeypatch):
    seen = []

    def fake_run_wfo(pair, bars, train_frac, grid, invert=False):
        seen.append(pair)
        return _fake_wfo_result(pair, verdict="holds")

    monkeypatch.setattr(wfo, "run_wfo", fake_run_wfo)

    fake_pairs = ["A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT"]
    monkeypatch.setattr(wfo, "PAIRS", fake_pairs)

    results = wfo.run_all_pairs(bars=500, train_frac=0.5, grid={})
    assert seen == fake_pairs
    assert len(results) == 3


def test_print_scoreboard_sorts_holds_before_overfit(monkeypatch, capsys):
    results = [
        _fake_wfo_result("OVERFIT/USDT:USDT", verdict="overfit"),
        _fake_wfo_result("HOLDS/USDT:USDT", verdict="holds"),
        _fake_wfo_result("NONE/USDT:USDT", verdict="no_winner"),
    ]
    wfo.print_scoreboard(results)
    captured = capsys.readouterr().out
    # Rank order from the code:
    #   ✅ holds (0) → no closures (1) → ❌ overfit (2) → no winner (3)
    holds_pos = captured.find("HOLDS/USDT:USDT")
    overfit_pos = captured.find("OVERFIT/USDT:USDT")
    none_pos = captured.find("NONE/USDT:USDT")
    assert holds_pos < overfit_pos < none_pos
    assert "✅ holds" in captured
    assert "❌ overfit" in captured


def test_print_scoreboard_handles_all_failed(monkeypatch, capsys):
    results = [_fake_wfo_result("X/USDT:USDT", verdict="no_winner")]
    wfo.print_scoreboard(results)
    captured = capsys.readouterr().out
    assert "no winner" in captured
