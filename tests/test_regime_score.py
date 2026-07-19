"""Unit tests for regime-adaptive deployment (regime_score + dynamic cap)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams, weight_path
from ictbot.strategy.regime_score import (
    adaptive_cap,
    adaptive_target_weights,
    cap_series,
    regime_labels,
    regime_score,
)

N = 280


def matrix(series: dict[str, np.ndarray]) -> pd.DataFrame:
    df = pd.DataFrame(series)
    df.insert(0, "time", pd.date_range("2024-01-01", periods=len(df), freq="4h"))
    return df.set_index("time")


def all_ramp(slope: float, base: float = 100.0) -> dict[str, np.ndarray]:
    return {t: base + slope * np.arange(N) for t in CONTEST_TOKENS}


def test_adaptive_cap_bounds():
    assert adaptive_cap(0.0, 0.40, 0.85) == 0.40
    assert adaptive_cap(1.0, 0.40, 0.85) == 0.85
    assert adaptive_cap(0.5, 0.40, 0.85) == 0.625
    assert adaptive_cap(2.0, 0.40, 0.85) == 0.85  # clipped above
    assert adaptive_cap(-1.0, 0.40, 0.85) == 0.40  # clipped below


def test_regime_score_higher_when_basket_trends_up():
    up = matrix(all_ramp(+0.5)).to_numpy()
    down = matrix(all_ramp(-0.5, base=300.0)).to_numpy()
    s_up = regime_score(up, N - 1)
    s_down = regime_score(down, N - 1)
    assert s_up > s_down
    assert s_up > 0.5 and s_down < 0.2  # broad up vs broad down


def test_high_vol_pulls_score_down():
    cols = all_ramp(+0.5)
    calm = matrix(cols).to_numpy()
    spiky = matrix(cols).to_numpy().copy()
    spiky[-1] = spiky[-1] * 1.30  # 30% index spike on the last bar
    assert regime_score(spiky, N - 1) < regime_score(calm, N - 1)


def test_regime_labels_bull_vs_bear():
    up = matrix(all_ramp(+0.5)).to_numpy()
    down = matrix(all_ramp(-0.5, base=300.0)).to_numpy()
    assert regime_labels(up)[-1] == "BULL"
    assert regime_labels(down)[-1] == "BEAR"


def test_adaptive_weights_deploy_in_bull_cash_in_bear():
    p = AllocatorParams(top_k=2)
    up = matrix(all_ramp(+0.5))
    w_up, score_up, cap_up = adaptive_target_weights(up, p, floor=0.40, ceiling=0.85)
    assert 0.40 <= cap_up <= 0.85
    assert sum(w_up.values()) > 0.40  # bull -> deployed near the ceiling
    assert sum(w_up.values()) == cap_up or abs(sum(w_up.values()) - cap_up) < 1e-9

    down = matrix(all_ramp(-0.5, base=300.0))
    w_down, _, _ = adaptive_target_weights(down, p, floor=0.40, ceiling=0.85)
    assert all(v == 0.0 for v in w_down.values())  # cash filter -> all USDT


def test_adaptive_weights_ta_rank_reduces_to_baseline_and_reorders():
    # CMC TA-confirmed ranking: inert at w_ta_rank=0 (regression), reorders when it tilts.
    p = AllocatorParams(top_k=2)
    cols = {t: 100.0 + (0.2 + 0.1 * i) * np.arange(N) for i, t in enumerate(CONTEST_TOKENS)}
    df = matrix(cols)
    base, _, _ = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85)
    neutral = {t: 0.5 for t in CONTEST_TOKENS}
    off, _, _ = adaptive_target_weights(
        df, p, floor=0.40, ceiling=0.85, ta_token_scores=neutral, w_ta_rank=0.0
    )
    assert base == off  # w_ta_rank=0 -> byte-identical baseline
    tilt = {t: 0.5 for t in CONTEST_TOKENS}
    tilt[CONTEST_TOKENS[0]] = 1.0  # confirm the LOW-momentum token
    tilt[CONTEST_TOKENS[-1]] = 0.0  # penalise the TOP-momentum token
    on, _, _ = adaptive_target_weights(
        df, p, floor=0.40, ceiling=0.85, ta_token_scores=tilt, w_ta_rank=10.0
    )
    held_base = {k for k, v in base.items() if v > 0}
    held_on = {k for k, v in on.items() if v > 0}
    assert on != base  # a strong TA tilt reorders the held set
    assert CONTEST_TOKENS[-1] in held_base  # top-momentum token held at baseline...
    assert CONTEST_TOKENS[-1] not in held_on  # ...penalised out by the TA tilt
    assert CONTEST_TOKENS[0] in held_on  # the strongly-confirmed token is now held


def test_constant_cap_series_matches_static_path():
    # the dynamic-cap plumbing with a CONSTANT cap must equal the static path
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0.05, 1.0, (N, len(CONTEST_TOKENS))), axis=0)
    caps = np.full(N, 0.5)
    dyn = weight_path(close, AllocatorParams(), cap_series=caps)
    static = weight_path(close, AllocatorParams(deploy_cap=0.5))
    assert np.allclose(dyn, static)


def test_cap_series_within_band():
    close = matrix(all_ramp(+0.3)).to_numpy()
    caps = cap_series(close, floor=0.40, ceiling=0.85)
    assert caps.shape[0] == N
    assert caps.min() >= 0.40 - 1e-9 and caps.max() <= 0.85 + 1e-9
