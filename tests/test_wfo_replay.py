"""Unit tests for the strategy-agnostic 4h walk-forward replay."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.engine.wfo_replay import (
    Trade,
    equity_drawdown,
    expectancy,
    replay,
    walk_forward,
)
from ictbot.strategy.trend_basket import TrendParams


def make_df(closes: np.ndarray) -> pd.DataFrame:
    n = len(closes)
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=n, freq="4h"),
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": np.ones(n),
        }
    )


def trend_with_pullbacks(n, slope, base=100.0, amp=3.0):
    i = np.arange(n)
    return base + slope * i + amp * np.sin(i * 2 * np.pi / 10.0)


def test_replay_produces_closed_trades():
    df = make_df(trend_with_pullbacks(300, slope=0.5))
    trades = replay(df, TrendParams(pullback_lookback=5))
    assert trades, "expected at least one closed trade"
    assert all(t.outcome in ("WIN", "LOSS", "BE") for t in trades)


def test_friction_reduces_net_below_gross():
    df = make_df(trend_with_pullbacks(300, slope=0.6))
    # heavy friction so the effect is unmistakable
    trades = replay(
        df, TrendParams(pullback_lookback=5), fee_per_side=0.002, slippage_per_side=0.002
    )
    assert trades
    for t in trades:
        assert t.friction_R > 0
        assert abs(t.net_R - (t.gross_R - t.friction_R)) < 1e-6
        assert t.net_R < t.gross_R + 1e-9


def test_expectancy_empty_and_nonempty():
    assert expectancy([]) == (None, 0)
    trades = [
        Trade(0, 1, "BUY", 2.0, 0.1, 1.9, "WIN"),
        Trade(2, 3, "BUY", -1.0, 0.1, -1.1, "LOSS"),
    ]
    exp, n = expectancy(trades)
    assert n == 2
    assert abs(exp - (1.9 - 1.1) / 2) < 1e-9


def test_equity_drawdown_detects_a_drop():
    # win then two losses -> equity peaks at trade 1 close, then draws down
    trades = [
        Trade(0, 5, "BUY", 2.0, 0.0, 2.0, "WIN"),
        Trade(6, 12, "BUY", -1.0, 0.0, -1.0, "LOSS"),
        Trade(13, 20, "BUY", -1.0, 0.0, -1.0, "LOSS"),
    ]
    max_dd, worst_7d = equity_drawdown(trades, start=0, end=25)
    assert max_dd > 0
    assert 0.0 <= worst_7d <= max_dd + 1e-9


def test_walk_forward_runs_and_returns_a_valid_verdict():
    df = make_df(trend_with_pullbacks(400, slope=0.4))
    res = walk_forward(df, train_frac=0.6)
    assert res["verdict"] in (
        "✅ holds",
        "❌ overfit",
        "no edge",
        "no closures",
        "small sample",
    )
    assert res["n_test"] > 0
    # when a TRAIN winner exists, the keys are populated
    if res.get("best_params") is not None:
        assert res["worst_7d_dd"] is not None
