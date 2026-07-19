"""
Tests for size.risk_of_ruin.
"""

import pytest

from ictbot.engine.sizing import risk_of_ruin


def test_no_edge_eventually_ruins():
    """50% win-rate at 1:1 RR = no edge → high ruin probability."""
    r = risk_of_ruin(win_rate_pct=50, rr=1.0, risk_per_trade=0.10, drawdown_target=0.5)
    # With 10% bet and no edge, ruin probability should be high (>50%)
    assert r["ruin_probability_pct"] > 50


def test_strong_edge_low_ruin():
    """60% win-rate at 1:3 RR, conservative 2% sizing → low ruin."""
    r = risk_of_ruin(win_rate_pct=60, rr=3.0, risk_per_trade=0.02, drawdown_target=0.5)
    assert r["ruin_probability_pct"] < 5


def test_full_kelly_overbet_high_ruin():
    """50% win-rate at 1:3, but BET 30% per trade (way above Kelly) → ruin."""
    r = risk_of_ruin(win_rate_pct=50, rr=3.0, risk_per_trade=0.30, drawdown_target=0.5)
    # Overbetting Kelly should produce frequent drawdowns
    assert r["ruin_probability_pct"] > 30


def test_smaller_drawdown_threshold_is_more_likely():
    """A 20% drawdown is easier to hit than a 50% one."""
    weak = risk_of_ruin(50, 1.5, 0.10, drawdown_target=0.2)
    severe = risk_of_ruin(50, 1.5, 0.10, drawdown_target=0.5)
    assert weak["ruin_probability_pct"] >= severe["ruin_probability_pct"]


def test_output_shape():
    r = risk_of_ruin(60, 3.0, 0.02)
    assert set(r.keys()) >= {
        "win_rate_pct",
        "rr",
        "risk_per_trade_pct",
        "drawdown_target_pct",
        "n_paths",
        "n_trades_per_path",
        "ruin_probability_pct",
        "median_final_x",
        "best_final_x",
        "worst_final_x",
    }


def test_invalid_inputs():
    with pytest.raises(ValueError):
        risk_of_ruin(-1, 1.0, 0.1)
    with pytest.raises(ValueError):
        risk_of_ruin(50, 1.0, 1.5)
    with pytest.raises(ValueError):
        risk_of_ruin(50, -1.0, 0.1)
