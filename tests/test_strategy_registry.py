"""Contract + equivalence tests for the pluggable strategy registry.

The load-bearing guarantee: routing the LOCKED momentum allocator through the
registry adapters produces results bit-for-bit identical to calling the underlying
functions directly. If this ever drifts, the contest default has changed — which
must never happen silently.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from ictbot.strategy import regime_score as rs
from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import (
    CONTEST_TOKENS,
    AllocatorParams,
    target_weights_now,
    weight_path,
)
from ictbot.strategy.registry import StratContext


def make_df(n: int = 320) -> pd.DataFrame:
    """Deterministic close matrix: varied slopes (some down, some up) + curvature so
    the momentum ranking, cash filter, and breadth terms all get exercised."""
    cols = {}
    for i, t in enumerate(CONTEST_TOKENS):
        slope = (i - 3) * 0.08
        cols[t] = 100.0 + slope * np.arange(n) + 5.0 * np.sin(np.arange(n) / 10.0 + i)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    return pd.DataFrame(cols, index=idx)


def test_registry_exposes_builtins():
    avail = registry.available()
    assert "momentum" in avail
    assert "momentum_adaptive" in avail


def test_unknown_strategy_raises():
    try:
        registry.get("does_not_exist")
    except KeyError as e:
        assert "does_not_exist" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError for unknown strategy")


def test_adaptive_equivalence_bitwise():
    df = make_df()
    p = AllocatorParams()
    for fg in (None, 40, 80):
        for active in (None, list(CONTEST_TOKENS), ["BNB", "ETH", "CAKE"]):
            exp_w, exp_s, exp_c = rs.adaptive_target_weights(
                df,
                p,
                floor=0.40,
                ceiling=0.85,
                ma_window=50,
                fear_greed=fg,
                intel=None,
                ta_health=None,
                w_ta=1.0,
                ta_token_scores=None,
                w_ta_rank=0.0,
                active=active,
            )
            ctx = StratContext(
                params=p,
                active=active,
                deploy_cap=0.60,
                floor=0.40,
                ceiling=0.85,
                ma_window=50,
                fear_greed=fg,
            )
            d = registry.get("momentum_adaptive").target_weights_now(df, ctx=ctx)
            assert d.weights == exp_w, (fg, active)
            assert d.score == exp_s
            assert d.cap == exp_c


def test_static_equivalence_bitwise():
    df = make_df()
    p = AllocatorParams()
    for active in (list(CONTEST_TOKENS), ["BNB", "ETH", "CAKE"]):
        rank_cols = [c for c in df.columns if c in set(active)] or list(df.columns)
        exp_w = target_weights_now(df[rank_cols], p)
        ctx = StratContext(params=p, active=active, deploy_cap=0.60)
        d = registry.get("momentum").target_weights_now(df, ctx=ctx)
        assert d.weights == exp_w, active
        assert d.score is None
        assert d.cap == 0.60


def test_weight_path_equivalence_bitwise():
    df = make_df()
    p = AllocatorParams()
    close = df.to_numpy(dtype=float)
    caps = rs.cap_series(close, floor=0.40, ceiling=0.85, ma_window=50)
    for cs in (None, caps):
        exp = weight_path(close, p, cs)
        for name in ("momentum", "momentum_adaptive"):
            got = registry.get(name).weight_path(close, p=p, cap_series=cs)
            assert np.array_equal(exp, got), name


def test_all_capability_arms_registered():
    for name in (
        "momentum",
        "momentum_adaptive",
        "momentum_fast",
        "dual_momentum",
        "rotation",
        "breakout",
        "mean_reversion",
        "momentum_voltarget",
        "momentum_mafilter",
    ):
        assert name in registry.available()


def test_all_strategies_obey_long_only_spot_invariants():
    """Every registered strategy must be long-only (weights ≥ 0) and spot (never > 100%
    deployed) — the contract the execution layer + 25% DD gate rely on."""
    df = make_df()
    close = df.to_numpy(dtype=float)
    caps = rs.cap_series(close, floor=0.40, ceiling=0.85, ma_window=50)
    ctx = StratContext(params=AllocatorParams(), floor=0.40, ceiling=0.85, ma_window=50)
    for name in registry.available():
        strat = registry.get(name)
        p = strat.default_params()
        wp = strat.weight_path(close, p=p, cap_series=caps)
        assert wp.shape == close.shape, name
        assert np.all(wp >= -1e-12), name  # long-only: no shorts
        assert np.all(wp.sum(axis=1) <= 1.0 + 1e-9), name  # spot: never levered
        d = strat.target_weights_now(df, ctx=ctx)
        assert all(v >= -1e-12 for v in d.weights.values()), name
        assert sum(d.weights.values()) <= 1.0 + 1e-9, name
        assert set(d.weights).issubset(set(CONTEST_TOKENS)), name


def test_dual_momentum_rising_matches_abs_filter():
    # All tokens rising -> basket up -> NO kill -> identical to the abs_filter=True path.
    n = 320
    cols = {t: 100.0 + (1.0 + 0.1 * i) * np.arange(n) for i, t in enumerate(CONTEST_TOKENS)}
    df = pd.DataFrame(cols, index=pd.date_range("2024-01-01", periods=n, freq="4h"))
    p = AllocatorParams()
    p2 = replace(p, abs_filter=True)
    exp_w, _, _ = rs.adaptive_target_weights(df, p2, floor=0.40, ceiling=0.85, ma_window=50)
    ctx = StratContext(params=p, floor=0.40, ceiling=0.85, ma_window=50)
    d = registry.get("dual_momentum").target_weights_now(df, ctx=ctx)
    assert d.weights == exp_w
    assert sum(d.weights.values()) > 0  # basket up -> deployed


def test_dual_momentum_falling_goes_all_cash():
    # All tokens falling -> basket down -> the basket kill forces ALL USDT (cap 0).
    n = 320
    cols = {t: 200.0 - (0.2 + 0.05 * i) * np.arange(n) for i, t in enumerate(CONTEST_TOKENS)}
    df = pd.DataFrame(cols, index=pd.date_range("2024-01-01", periods=n, freq="4h"))
    ctx = StratContext(params=AllocatorParams(), floor=0.40, ceiling=0.85, ma_window=50)
    d = registry.get("dual_momentum").target_weights_now(df, ctx=ctx)
    assert all(v == 0.0 for v in d.weights.values())  # fully USDT
    assert d.cap == 0.0  # the kill sets cap to 0


def test_momentum_fast_uses_short_horizon():
    dp = registry.get("momentum_fast").default_params()
    assert dp.lookback == 60
    assert dp.rebal_bars == 3
    df = make_df()
    close = df.to_numpy(dtype=float)
    fast = registry.get("momentum_fast").weight_path(close, p=AllocatorParams())
    slow = registry.get("momentum_adaptive").weight_path(close, p=AllocatorParams())
    assert fast.shape == slow.shape
    assert not np.array_equal(fast, slow)  # short lookback/faster rebal -> different book
