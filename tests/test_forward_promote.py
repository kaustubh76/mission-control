"""Forward-promotion check (scripts/forward_promote.py): per-strategy forward stats
+ Part 7 eligibility, and the insufficient-data degrade."""

from __future__ import annotations

from datetime import datetime, timedelta

import scripts.forward_promote as fp


def _rows(navs, swaps_each=2, step_days=1.0, start="2026-05-01T00:00:00+00:00"):
    t0 = datetime.fromisoformat(start)
    return [(t0 + timedelta(days=i * step_days), float(n), swaps_each) for i, n in enumerate(navs)]


def test_insufficient_rows_returns_none():
    assert fp._forward_stats(_rows([1000, 1001, 1002])) is None  # < MIN_ROWS


def test_insufficient_span_returns_none():
    # 12 rows but only ~11 days apart-ish total span < 14d (step 1 day → 11d span)
    assert fp._forward_stats(_rows(list(range(1000, 1012))[:12], step_days=1.0)) is None


def test_rising_track_is_eligible():
    navs = [1000 * (1.01**i) for i in range(20)]  # steady +1%/tick over 20 days
    stats = fp._forward_stats(_rows(navs))
    assert stats is not None
    assert stats["worst_7d_dd"] < 0.25
    assert stats["trades_per_week"] >= 7  # 2 swaps/day → ~14/wk
    assert stats["median_weekly_ret"] >= 0
    assert fp._eligible(stats) is True


def test_declining_track_not_eligible():
    navs = [1000 * (0.99**i) for i in range(20)]  # steady -1%/tick
    stats = fp._forward_stats(_rows(navs))
    assert stats is not None
    assert stats["median_weekly_ret"] < 0  # negative forward return
    assert fp._eligible(stats) is False


def test_sparse_buckets_none_median_is_evaluable_but_not_eligible():
    # 10 rows spaced 10 days apart: >= MIN_ROWS and span >> min_days (so evaluable), but every
    # consecutive 7-day bucket holds < 2 rows → no weekly returns → median is None. _eligible
    # must hold the line via its `mwr is not None` guard even though DD + t/wk would pass.
    rows = _rows(
        [1000 + i for i in range(10)], swaps_each=20, step_days=10.0
    )  # rising, dense swaps
    stats = fp._forward_stats(rows, min_days=5)
    assert stats is not None
    assert stats["median_weekly_ret"] is None
    assert stats["worst_7d_dd"] < 0.25 and stats["trades_per_week"] >= 7  # only median disqualifies
    assert fp._eligible(stats) is False


def test_min_days_override_makes_short_span_evaluable():
    # 12 rising rows, 1 day apart → ~11-day span: insufficient at the rigorous 14d window,
    # but evaluable at the contest-compressed 5d window the campaign uses.
    rows = _rows([1000 * (1.002**i) for i in range(12)], step_days=1.0)
    assert fp._forward_stats(rows, min_days=14) is None
    s = fp._forward_stats(rows, min_days=5)
    assert s is not None and s["span_days"] >= 5
    assert (
        fp._verdict_for(
            [
                {
                    "event": "REBALANCE",
                    "strategy": "rotation",
                    "ts": t.isoformat(),
                    "nav_after": n,
                    "n_swaps": sw,
                }
                for (t, n, sw) in rows
            ],
            "rotation",
            min_days=5,
        )["status"]
        == "evaluated"
    )


def test_verdict_for_filters_by_strategy_and_degrades():
    # Journal-shaped rows: only 3 ticks for "rotation" → insufficient.
    journal = [
        {
            "event": "REBALANCE",
            "strategy": "rotation",
            "ts": "2026-05-01T00:00:00+00:00",
            "nav_after": 1000,
            "n_swaps": 2,
        },
        {
            "event": "REBALANCE",
            "strategy": "rotation",
            "ts": "2026-05-02T00:00:00+00:00",
            "nav_after": 1005,
            "n_swaps": 2,
        },
        {
            "event": "REBALANCE",
            "strategy": "momentum_adaptive",
            "ts": "2026-05-02T00:00:00+00:00",
            "nav_after": 1003,
            "n_swaps": 1,
        },
    ]
    v = fp._verdict_for(journal, "rotation")
    assert v["status"] == "insufficient forward data"
    assert v["forward_eligible"] is False
