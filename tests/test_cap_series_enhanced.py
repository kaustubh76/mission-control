"""
`cap_series_enhanced` tests — makes the enhanced regime BACKTESTABLE.

The critical guard: with macro absent it is bit-for-bit the validated `cap_series`
(protects the contest path). The direction tests prove the macro terms move the
deploy cap the intended way (so a real A/B is measuring a real effect).
"""

from __future__ import annotations

import numpy as np

from ictbot.strategy.regime_score import cap_series, cap_series_enhanced

FLOOR, CEIL = 0.40, 0.85


def _close(n: int = 300, k: int = 8, slope: float = -0.08) -> np.ndarray:
    """Mild downtrend (baseline regime score unsaturated → macro terms can move it)."""
    col = np.arange(n)[:, None] * slope + 300.0
    return np.tile(col, (1, k)) + (np.arange(k)[None, :] * 0.3 - 1.0)


def test_reduces_to_baseline_when_macro_absent():
    c = _close()
    assert np.allclose(
        cap_series(c, floor=FLOOR, ceiling=CEIL, ma_window=50),
        cap_series_enhanced(c, floor=FLOOR, ceiling=CEIL, ma_window=50),
    )  # all macro None


def test_nan_macro_reduces_to_baseline():
    c = _close()
    n = c.shape[0]
    enh = cap_series_enhanced(
        c,
        floor=FLOOR,
        ceiling=CEIL,
        dominance=np.full(n, np.nan),
        dominance_prev=np.full(n, np.nan),
    )
    assert np.allclose(cap_series(c, floor=FLOOR, ceiling=CEIL), enh)  # NaN terms self-disable


def test_zero_weights_no_fng_reduce_to_baseline():
    c = _close()
    n = c.shape[0]
    enh = cap_series_enhanced(
        c,
        floor=FLOOR,
        ceiling=CEIL,
        dominance=np.full(n, 50.0),
        dominance_prev=np.full(n, 60.0),
        w_dominance=0.0,
        w_mktcap=0.0,
        w_fng_mom=0.0,
    )
    assert np.allclose(cap_series(c, floor=FLOOR, ceiling=CEIL), enh)  # the (0,0,0) sweep anchor


def test_falling_dominance_gives_higher_cap_than_rising():
    c = _close()
    n = c.shape[0]
    fall = cap_series_enhanced(
        c,
        floor=FLOOR,
        ceiling=CEIL,
        dominance=np.full(n, 50.0),
        dominance_prev=np.full(n, 60.0),
        w_mktcap=0.0,
        w_fng_mom=0.0,
    )
    rise = cap_series_enhanced(
        c,
        floor=FLOOR,
        ceiling=CEIL,
        dominance=np.full(n, 60.0),
        dominance_prev=np.full(n, 50.0),
        w_mktcap=0.0,
        w_fng_mom=0.0,
    )
    assert fall.mean() > rise.mean()


def test_expanding_mktcap_gives_higher_cap_than_contracting():
    c = _close()
    n = c.shape[0]
    exp = cap_series_enhanced(
        c,
        floor=FLOOR,
        ceiling=CEIL,
        mktcap=np.full(n, 2.4e12),
        mktcap_prev=np.full(n, 2.0e12),
        w_dominance=0.0,
        w_fng_mom=0.0,
    )
    con = cap_series_enhanced(
        c,
        floor=FLOOR,
        ceiling=CEIL,
        mktcap=np.full(n, 1.6e12),
        mktcap_prev=np.full(n, 2.0e12),
        w_dominance=0.0,
        w_fng_mom=0.0,
    )
    assert exp.mean() > con.mean()


def test_ta_health_absent_reduces_to_baseline():
    c = _close()
    assert np.allclose(
        cap_series(c, floor=FLOOR, ceiling=CEIL),
        cap_series_enhanced(c, floor=FLOOR, ceiling=CEIL, ta_health=None),
    )


def test_higher_ta_health_gives_higher_cap():
    c = _close()
    n = c.shape[0]
    healthy = cap_series_enhanced(c, floor=FLOOR, ceiling=CEIL, ta_health=np.full(n, 0.9))
    weak = cap_series_enhanced(c, floor=FLOOR, ceiling=CEIL, ta_health=np.full(n, 0.1))
    assert healthy.mean() > weak.mean()  # the TA trend-health term lifts the cap
