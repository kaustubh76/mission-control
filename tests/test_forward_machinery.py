"""Forward-promotion machinery on JOURNAL-shaped rows (the real shape run_allocator writes):
known-answer NAV tracks end-to-end through forward_promote._verdict_for, plus the boundary cases
(MIN_ROWS, span==min_days, median==0, non-positive NAV, non-REBALANCE rows). Complements
test_forward_promote.py, which exercises the tuple-level math."""

from __future__ import annotations

from datetime import datetime, timedelta

import scripts.forward_promote as fp


def _journal(name, navs, *, swaps=2, step_days=1.0, start="2026-05-01T00:00:00+00:00"):
    t0 = datetime.fromisoformat(start)
    return [
        {
            "event": "REBALANCE",
            "strategy": name,
            "ts": (t0 + timedelta(days=i * step_days)).isoformat(),
            "nav_after": float(n),
            "n_swaps": swaps,
        }
        for i, n in enumerate(navs)
    ]


def test_rising_track_eligible():
    v = fp._verdict_for(_journal("x", [1000 * (1.01**i) for i in range(20)]), "x", min_days=14)
    assert v["status"] == "evaluated" and v["forward_eligible"] is True
    assert v["worst_7d_dd"] < 0.25 and v["trades_per_week"] >= 7 and v["median_weekly_ret"] >= 0


def test_declining_track_not_eligible():
    v = fp._verdict_for(_journal("x", [1000 * (0.99**i) for i in range(20)]), "x")
    assert v["status"] == "evaluated" and v["forward_eligible"] is False
    assert v["median_weekly_ret"] < 0


def test_flat_track_median_zero_is_eligible():
    # flat NAV → every weekly bucket return is exactly 0; 0 >= 0 satisfies the non-negative rule.
    v = fp._verdict_for(_journal("x", [1000.0] * 20), "x")
    assert v["status"] == "evaluated"
    assert v["median_weekly_ret"] == 0.0 and v["worst_7d_dd"] == 0.0
    assert v["forward_eligible"] is True


def test_only_named_strategy_counted():
    j = _journal("x", [1000 + i for i in range(20)]) + _journal("y", [500.0] * 20)
    assert fp._verdict_for(j, "x")["n_rows"] == 20


def test_below_min_rows_insufficient():
    j = _journal("x", list(range(1000, 1009)), step_days=2.0)  # 9 rows < MIN_ROWS(10)
    assert fp._forward_stats(fp._strategy_rows(j, "x")) is None
    assert fp._verdict_for(j, "x")["status"] == "insufficient forward data"


def test_span_boundary_at_min_days():
    short = _journal("x", [1000 + i for i in range(10)], step_days=0.5)  # span 4.5d
    wide = _journal("x", [1000 + i for i in range(10)], step_days=0.6)  # span 5.4d
    assert fp._verdict_for(short, "x", min_days=5)["status"] == "insufficient forward data"
    assert fp._verdict_for(wide, "x", min_days=5)["status"] == "evaluated"


def test_nonpositive_nav_rows_dropped():
    j = _journal("x", [1000.0] * 20)
    j += [
        {
            "event": "REBALANCE",
            "strategy": "x",
            "ts": "2026-06-01T00:00:00+00:00",
            "nav_after": 0,
            "n_swaps": 2,
        },
        {
            "event": "REBALANCE",
            "strategy": "x",
            "ts": "2026-06-02T00:00:00+00:00",
            "nav_after": -5,
            "n_swaps": 2,
        },
    ]
    rows = fp._strategy_rows(j, "x")
    assert len(rows) == 20 and all(nav > 0 for _, nav, _ in rows)


def test_non_rebalance_rows_ignored():
    j = _journal("x", [1000.0] * 12)
    j.append(
        {
            "event": "DD_HALT",
            "strategy": "x",
            "ts": "2026-06-01T00:00:00+00:00",
            "nav_after": 999,
            "n_swaps": 0,
        }
    )
    assert len(fp._strategy_rows(j, "x")) == 12
