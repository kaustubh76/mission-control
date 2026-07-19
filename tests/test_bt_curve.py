"""
Tests for bt_curve.build_curve.
"""

import pandas as pd

from ictbot.engine.bt_curve import build_curve


def _fake_report(signals: list) -> dict:
    return {
        "pair": "TEST",
        "bars_scanned": 100,
        "counts": {},
        "signals": signals,
        "near_misses": [],
        "verbose": False,
    }


def test_build_curve_skips_open_trades():
    sigs = [
        {
            "outcome": "WIN",
            "net_R": +0.5,
            "time": pd.Timestamp("2026-01-01"),
            "closed_at": pd.Timestamp("2026-01-01 01:00"),
        },
        {
            "outcome": "OPEN",
            "net_R": 0.0,
            "time": pd.Timestamp("2026-01-01 02:00"),
            "closed_at": None,
        },
        {
            "outcome": "LOSS",
            "net_R": -1.2,
            "time": pd.Timestamp("2026-01-01 03:00"),
            "closed_at": pd.Timestamp("2026-01-01 04:00"),
        },
    ]
    curve = build_curve(_fake_report(sigs))
    assert len(curve) == 2
    outcomes = [p["outcome"] for p in curve]
    assert "OPEN" not in outcomes


def test_build_curve_is_cumulative_in_chronological_order():
    sigs = [
        {
            "outcome": "WIN",
            "net_R": +0.5,
            "time": pd.Timestamp("2026-01-01"),
            "closed_at": pd.Timestamp("2026-01-01 01:00"),
        },
        {
            "outcome": "LOSS",
            "net_R": -1.0,
            "time": pd.Timestamp("2026-01-01 02:00"),
            "closed_at": pd.Timestamp("2026-01-01 03:00"),
        },
        {
            "outcome": "WIN",
            "net_R": +0.8,
            "time": pd.Timestamp("2026-01-01 04:00"),
            "closed_at": pd.Timestamp("2026-01-01 05:00"),
        },
    ]
    curve = build_curve(_fake_report(sigs))
    assert curve[0]["cum_R"] == 0.5
    assert curve[1]["cum_R"] == -0.5
    assert abs(curve[2]["cum_R"] - 0.3) < 1e-9


def test_build_curve_empty_for_no_closures():
    sigs = [{"outcome": "OPEN", "net_R": 0, "time": pd.Timestamp("2026-01-01"), "closed_at": None}]
    assert build_curve(_fake_report(sigs)) == []
