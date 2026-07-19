"""Unit tests for the portfolio-level backtest engine."""

from __future__ import annotations

import numpy as np

from ictbot.engine.portfolio_replay import (
    BARS_PER_WEEK,
    curve_metrics,
    evaluate,
    returns_matrix,
    rolling_window_stats,
    simulate,
)


def test_simulate_grows_on_constant_positive_returns_and_pays_friction():
    n, k = 300, 2
    close = np.cumprod(np.full((n, k), 1.01), axis=0) * 100  # +1%/bar both assets
    rets = returns_matrix(close)
    full = np.tile([0.5, 0.5], (n, 1))  # always fully invested
    eq_nofric, _ = simulate(full, rets, one_way=0.0)
    eq_fric, txns = simulate(full, rets, one_way=0.0030)
    assert eq_nofric[-1] > eq_nofric[0]  # compounding works
    assert eq_fric[-1] < eq_nofric[-1]  # friction is a drag
    assert txns >= 2  # initial allocation traded


def test_no_turnover_after_first_bar_costs_nothing_more():
    n, k = 100, 2
    close = np.cumprod(np.full((n, k), 1.005), axis=0) * 100
    rets = returns_matrix(close)
    w = np.tile([0.5, 0.5], (n, 1))
    _, txns = simulate(w, rets, one_way=0.01)
    # weights never change after bar 1 -> only the first allocation counts
    assert txns == 2


def test_rolling_window_stats_on_a_known_curve():
    # monotile up then a sharp dip -> a measurable worst-window drawdown
    eq = np.concatenate([np.linspace(1.0, 2.0, 100), np.linspace(2.0, 1.5, 50)])
    s = rolling_window_stats(eq, warmup=0, win=BARS_PER_WEEK)
    assert s["n_windows"] > 0
    assert 0.0 <= s["worst_week_dd"] <= 1.0
    assert s["pct_dd_over_30"] <= 1.0
    assert "p5_ret" in s and "p95_ret" in s


def test_curve_metrics_basic():
    eq = np.array([1.0, 1.2, 0.9, 1.1])
    m = curve_metrics(eq)
    assert abs(m["total_return"] - 0.1) < 1e-9
    assert abs(m["max_dd"] - (1.2 - 0.9) / 1.2) < 1e-9


def test_evaluate_returns_expected_keys():
    n, k = 400, 3
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, (n, k)), axis=0)
    wp = np.tile([0.3, 0.3, 0.3], (n, 1))
    s = evaluate(close, wp, one_way=0.0015, warmup=160)
    for key in ("median_ret", "worst_week_dd", "trades_per_week", "total_return", "max_dd"):
        assert key in s
