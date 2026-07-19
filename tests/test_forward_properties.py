"""Property-based invariants for forward_promote: _forward_stats output bounds + the
None-return contract, and _eligible ⟺ the three documented conditions. Hypothesis."""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

import scripts.forward_promote as fp

POS = st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False)


def _rows(navs, step_days):
    t0 = datetime(2026, 5, 1)
    return [(t0 + timedelta(days=i * step_days), float(n), 2) for i, n in enumerate(navs)]


@given(navs=st.lists(POS, min_size=0, max_size=40), step_days=st.floats(0.5, 20.0))
@settings(deadline=None)
def test_forward_stats_bounds_and_none_contract(navs, step_days):
    rows = _rows(navs, step_days)
    stats = fp._forward_stats(rows, min_days=5)
    if stats is None:
        span = (rows[-1][0] - rows[0][0]).total_seconds() / 86400.0 if len(rows) >= 2 else 0.0
        assert len(rows) < fp.MIN_ROWS or span < 5  # the only two reasons to return None
    else:
        assert 0.0 <= stats["worst_7d_dd"] <= 1.0
        assert stats["trades_per_week"] >= 0.0
        assert stats["n_rows"] == len(rows)


@given(
    dd=st.floats(0.0, 1.0), tpw=st.floats(0.0, 40.0), mwr=st.one_of(st.none(), st.floats(-0.5, 0.5))
)
@settings(deadline=None)
def test_eligible_iff_three_conditions(dd, tpw, mwr):
    stats = {"worst_7d_dd": dd, "trades_per_week": tpw, "median_weekly_ret": mwr}
    expected = (
        dd < fp.GATE.max_worst_week_dd
        and tpw >= fp.GATE.min_trades_per_week
        and mwr is not None
        and mwr >= 0.0
    )
    assert fp._eligible(stats) is expected
