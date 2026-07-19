"""momentum_cmc adapter MECHANISM. The CMC-driven arm is momentum_adaptive's machinery fed CMC's
own candles (candle_source="cmc_4h"), plus ONE safety override: a daily-derived inverse-vol FLOOR
that tames the cold-start seed pathology. The cold-start seed forward-fills each CMC daily close
across the six 4h slots of its day, so a token whose recent window is (near-)constant gets std->~0
-> 1/vol explodes -> it captures ~100% of the deployment on a seed artifact. The floor clamps each
token's vol at the cross-token median DAILY return-std rescaled to the 4h bar (1/sqrt6), restoring a
sane split. Crucially momentum_adaptive (vol_floor=0.0) is untouched. Offline — synthetic, no network."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import AllocatorParams, _weights_at


def _ctx(**over):
    base = dict(
        params=AllocatorParams(top_k=4, abs_filter=False, vol_lookback=30),
        active=None,
        deploy_cap=0.60,
        floor=0.35,
        ceiling=0.80,
        ma_window=50,
        fear_greed=None,
        intel=None,
        ta_health=None,
        w_ta=0.0,
        ta_token_scores=None,
        w_ta_rank=0.0,
    )
    base.update(over)
    return registry.StratContext(**base)


def _seed_matrix(near_constant_token: int | None = None) -> pd.DataFrame:
    """A flat-intrabar seed matrix: per-token daily walk, each daily close forward-filled across the
    six 4h slots (exactly what seed_cmc_4h_from_daily writes). Optionally make one token dead-flat in
    the last 30 4h bars (the genuine blow-up case a quiet/seed window produces)."""
    toks = ["BNB", "ETH", "LINK", "UNI"]
    daily_vol = [0.02, 0.03, 0.04, 0.03]
    days = 60
    rng = np.random.default_rng(3)
    idx = pd.date_range("2026-04-01", periods=days * 6, freq="4h", tz="UTC")
    cols = {}
    for j, t in enumerate(toks):
        dp = [100.0]
        for _ in range(days - 1):
            dp.append(dp[-1] * (1 + rng.normal(0, daily_vol[j])))
        cols[t] = np.repeat(dp, 6)  # forward-fill across the six 4h slots
    mat = pd.DataFrame(cols, index=idx)
    if near_constant_token is not None:
        col = toks[near_constant_token]
        mat.iloc[-30:, mat.columns.get_loc(col)] = mat.iloc[-31, mat.columns.get_loc(col)]
    return mat


def test_vol_floor_default_is_a_strict_noop():
    """max(std, 0.0) == std -> the locked allocator is byte-identical at the default vol_floor=0.0."""
    close = np.cumprod(1 + np.random.default_rng(1).normal(0, 0.02, size=(200, 5)), axis=0) * 100.0
    rets = np.vstack([np.zeros(5), close[1:] / close[:-1] - 1.0])
    p0 = AllocatorParams(top_k=3, abs_filter=False)
    p_floor0 = replace(p0, vol_floor=0.0)
    w_legacy = _weights_at(close, rets, 199, p0, cap=0.60)
    w_floor0 = _weights_at(close, rets, 199, p_floor0, cap=0.60)
    assert np.array_equal(w_legacy, w_floor0)


def test_cmc_floor_tames_the_seed_blowup():
    """With one near-constant token, legacy inverse-vol (floor=0) hands it ~100% of deployment; the
    CMC arm's daily-derived floor restores a non-degenerate multi-token split."""
    mat = _seed_matrix(near_constant_token=3)
    ctx = _ctx()
    rets = np.vstack([np.zeros(mat.shape[1]), mat.values[1:] / mat.values[:-1] - 1.0])

    # legacy: vol_floor=0 -> the flat token dominates
    w_legacy = _weights_at(mat.values, rets, mat.shape[0] - 1, ctx.params, cap=ctx.deploy_cap)
    legacy_dom = max(w_legacy) / ctx.deploy_cap

    # CMC arm: target_weights_now injects the daily-derived floor
    cmc = registry.get("momentum_cmc")
    assert cmc._vol_floor(mat) > 0  # a real floor was derived from CMC daily vol
    w_cmc = cmc.target_weights_now(mat, ctx=ctx).weights
    held = [v for v in w_cmc.values() if v > 1e-9]

    assert legacy_dom > 0.95  # the bug: one token captures ~all deployment
    assert len(held) >= 3  # the fix: the book is spread across the held set
    assert max(held) / ctx.deploy_cap < 0.85  # …and no single token dominates


def test_momentum_adaptive_unchanged_on_the_same_seed_matrix():
    """The locked arm never sets a vol_floor, so its book on the seed matrix is exactly the
    vol_floor=0.0 path — proving the CMC override does not leak into momentum_adaptive."""
    mat = _seed_matrix(near_constant_token=3)
    ctx = _ctx()
    adaptive = registry.get("momentum_adaptive").target_weights_now(mat, ctx=ctx).weights
    rets = np.vstack([np.zeros(mat.shape[1]), mat.values[1:] / mat.values[:-1] - 1.0])
    w_legacy = _weights_at(mat.values, rets, mat.shape[0] - 1, ctx.params, cap=ctx.deploy_cap)
    # adaptive's deploy cap is regime-driven, but its INTRA-book split must match legacy inverse-vol.
    a = np.array([adaptive.get(c, 0.0) for c in mat.columns])
    if a.sum() > 0 and w_legacy.sum() > 0:
        assert np.allclose(a / a.sum(), w_legacy / w_legacy.sum(), atol=1e-9)


def test_cmc_candle_source_is_cmc_4h():
    assert registry.get("momentum_cmc").candle_source == "cmc_4h"
