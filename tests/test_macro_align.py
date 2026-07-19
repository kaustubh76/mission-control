"""
Daily CMC macro → 4h candle-index alignment tests (pure, no network).

These guard the load-bearing alignment for the backtest A/B: ffill correctness (no
lookahead), the ~30-day-ago baseline lookback, the trailing 7-day F&G average, and the
leading-NaN region (bars before the macro begins → reduce to baseline downstream).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy.macro_align import align_macro_to_index


def _synthetic():
    """60 4h bars = 10 days × 6 bars; daily macro values keyed to the day number."""
    idx = pd.date_range("2026-01-01", periods=60, freq="4h", tz="UTC")
    days = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    gm = [
        {
            "ts": int(d.timestamp()),
            "btc_dominance": 50.0 + i + 1,
            "total_market_cap": (2.0 + 0.1 * (i + 1)) * 1e12,
        }
        for i, d in enumerate(days)
    ]
    fng = [{"ts": int(d.timestamp()), "value": 10 + i + 1} for i, d in enumerate(days)]
    return idx, gm, fng


def test_ffill_same_within_day():
    idx, gm, fng = _synthetic()
    am = align_macro_to_index(idx, gm, fng, prev_days=3)
    assert all(am.dominance[j] == 51.0 for j in range(6))  # day 1 → 51, all 6 bars
    assert all(am.dominance[j] == 52.0 for j in range(6, 12))  # day 2 → 52


def test_prev_lookback_is_backward_shift():
    idx, gm, fng = _synthetic()
    am = align_macro_to_index(idx, gm, fng, prev_days=3)
    # day 5 (bars 24..29): dominance 55, prev = day 2 = 52
    assert am.dominance[24] == 55.0 and am.dominance_prev[24] == 52.0
    # days 1-3 have no 3-day-ago baseline → NaN (no lookahead, no fabrication)
    assert np.isnan(am.dominance_prev[0])


def test_fng_7d_trailing_mean():
    idx, gm, fng = _synthetic()
    am = align_macro_to_index(idx, gm, fng, fng_avg_days=7)
    # day 8 (bars 42..47): value 18; trailing 7 days = days 2..8 = 12..18 → mean 15
    assert abs(am.fng[42] - 18.0) < 1e-9
    assert abs(am.fng_7d[42] - 15.0) < 1e-9


def test_leading_bars_nan_before_macro():
    _, gm, fng = _synthetic()
    idx2 = pd.date_range("2025-12-30", periods=60, freq="4h", tz="UTC")  # starts before macro
    am = align_macro_to_index(idx2, gm, fng)
    assert np.isnan(am.dominance[0])  # 2025-12-30 < macro start


def test_empty_macro_is_all_nan():
    idx, _, _ = _synthetic()
    am = align_macro_to_index(idx, [], [])
    assert not am.any_present()
    assert np.isnan(am.dominance).all() and np.isnan(am.fng).all()


def test_tz_naive_index_is_handled():
    idx, gm, fng = _synthetic()
    naive = idx.tz_localize(None)  # candles can be tz-naive
    am = align_macro_to_index(naive, gm, fng)
    assert am.dominance[0] == 51.0 and len(am.dominance) == 60
