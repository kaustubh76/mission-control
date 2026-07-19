"""Tests for the shared Gate-A acceptance gate (engine/acceptance.py).

Pins the 25% operational drawdown ceiling: a strategy may use up to 25% worst-week
DD but never beyond it — strictly inside the 30% contest DQ line.
"""

from __future__ import annotations

from ictbot.engine.acceptance import (
    DEFAULT,
    evaluate_basket,
    evaluate_portfolio,
    evaluate_walk_forward,
)


def test_dd_ceiling_is_25_pct():
    assert DEFAULT.max_worst_week_dd == 0.25
    assert DEFAULT.dq_line == 0.30
    assert DEFAULT.target_worst_week_dd == 0.15


def test_portfolio_pass_within_ceiling():
    g = evaluate_portfolio({"worst_week_dd": 0.20, "trades_per_week": 10})
    assert g.passed and g.dq_safe and g.active
    assert g.metrics["within_dq_line"] is True
    assert g.metrics["target_dd_met"] is False  # 20% > 15% stretch target


def test_portfolio_fail_beyond_25_even_if_under_dq():
    # 26% DD is under the 30% DQ line but BEYOND the 25% operational ceiling -> FAIL.
    g = evaluate_portfolio({"worst_week_dd": 0.26, "trades_per_week": 10})
    assert not g.passed
    assert not g.dq_safe
    assert g.metrics["within_dq_line"] is True
    assert any("25%" in r for r in g.reasons)


def test_portfolio_fail_when_inactive():
    g = evaluate_portfolio({"worst_week_dd": 0.10, "trades_per_week": 3})
    assert g.dq_safe and not g.active and not g.passed


def test_basket_holders_and_trades():
    per_pair = {
        "A": {"verdict": "✅ holds", "worst_7d_dd": 0.10},
        "B": {"verdict": "✅ holds", "worst_7d_dd": 0.12},
        "C": {"verdict": "❌ overfit", "worst_7d_dd": 0.05},
        "D": {"verdict": "✅ holds", "worst_7d_dd": 0.20},  # DD over the 15% target -> not a holder
    }
    g = evaluate_basket(per_pair, basket_tpw=8.0)
    assert g.passed
    assert set(g.metrics["holders"]) == {"A", "B"}


def test_walk_forward_holds():
    g = evaluate_walk_forward(
        {"train_exp": 0.3, "test_exp": 0.2, "test_closures": 15, "worst_7d_dd": 0.10}
    )
    assert g.passed and g.active and g.dq_safe
