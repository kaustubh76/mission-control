"""Tests for the overlay framework + the vol_target / ma_filter overlays.

The load-bearing guarantee mirrors the registry's: with an identity overlay the
wrapped strategy is bit-for-bit the base. Plus the contraction invariant
`sum(row_after) <= sum(row_before)` for every real overlay (spot is long-only —
overlays may only de-risk, never lever up).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy import regime_score as rs
from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.overlays.base import IdentityOverlay, OverlayStrategy
from ictbot.strategy.registry import StratContext


def make_df(n: int = 320) -> pd.DataFrame:
    cols = {}
    for i, t in enumerate(CONTEST_TOKENS):
        slope = (i - 3) * 0.08
        cols[t] = 100.0 + slope * np.arange(n) + 5.0 * np.sin(np.arange(n) / 10.0 + i)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    return pd.DataFrame(cols, index=idx)


def test_identity_overlay_weight_path_bitwise():
    df = make_df()
    p = AllocatorParams()
    close = df.to_numpy(dtype=float)
    caps = rs.cap_series(close, floor=0.40, ceiling=0.85, ma_window=50)
    base = registry.get("momentum_adaptive")
    wrapped = OverlayStrategy(base, [IdentityOverlay()], name="momentum_identity")
    for cs in (None, caps):
        assert np.array_equal(
            wrapped.weight_path(close, p=p, cap_series=cs),
            base.weight_path(close, p=p, cap_series=cs),
        )


def test_identity_overlay_live_bitwise():
    df = make_df()
    p = AllocatorParams()
    base = registry.get("momentum_adaptive")
    wrapped = OverlayStrategy(base, [IdentityOverlay()], name="momentum_identity")
    for fg in (None, 50):
        ctx = StratContext(params=p, floor=0.40, ceiling=0.85, ma_window=50, fear_greed=fg)
        d_base = base.target_weights_now(df, ctx=ctx)
        d_wrap = wrapped.target_weights_now(df, ctx=ctx)
        assert d_wrap.weights == d_base.weights
        assert d_wrap.score == d_base.score
        assert d_wrap.cap == d_base.cap


def test_overlay_warmup_is_max():
    class W50:
        name = "w50"

        def apply_path(self, wp, close, *, p):
            return wp

        def apply_now(self, w, *, close_df, cap, ctx):
            return w, cap

        def warmup(self, p):
            return 50

        def summary(self):
            return "w50"

    base = registry.get("momentum")
    wrapped = OverlayStrategy(base, [W50()], name="x")
    p = AllocatorParams()
    assert wrapped.warmup(p) == max(base.warmup(p), 50)


def test_overlay_chaining_order():
    calls = []

    def mk(tag):
        class _Ov:
            name = tag

            def apply_path(self, wp, close, *, p):
                calls.append(tag)
                return wp

            def apply_now(self, w, *, close_df, cap, ctx):
                return w, cap

            def warmup(self, p):
                return 0

            def summary(self):
                return tag

        return _Ov()

    df = make_df()
    base = registry.get("momentum")
    wrapped = OverlayStrategy(base, [mk("a"), mk("b")], name="ab")
    wrapped.weight_path(df.to_numpy(dtype=float), p=AllocatorParams())
    assert calls == ["a", "b"]  # left-to-right, after the base


# --- vol_target overlay ----------------------------------------------------- #


def _base_path(df, cs=None):
    p = AllocatorParams()
    return registry.get("momentum_adaptive").weight_path(
        df.to_numpy(dtype=float), p=p, cap_series=cs
    )


def test_vol_target_identity_when_target_high():
    from ictbot.strategy.overlays.vol_target import VolTargetOverlay

    df = make_df()
    close = df.to_numpy(dtype=float)
    # target_vol huge -> s clamps to 1.0 everywhere -> bit-for-bit base path.
    ov = VolTargetOverlay(target_vol=1e9, vol_lookback=30)
    assert np.array_equal(ov.apply_path(_base_path(df), close, p=AllocatorParams()), _base_path(df))


def test_vol_target_contracts_and_never_levers():
    from ictbot.strategy.overlays.vol_target import VolTargetOverlay

    df = make_df()
    close = df.to_numpy(dtype=float)
    base = _base_path(df)
    # target far below the fixture's realized vol -> s < 1 -> guaranteed de-risk.
    out = VolTargetOverlay(target_vol=1e-5, vol_lookback=30).apply_path(
        base, close, p=AllocatorParams()
    )
    # Contraction invariant: every row total can only shrink.
    assert np.all(out.sum(axis=1) <= base.sum(axis=1) + 1e-12)
    assert np.all(out.sum(axis=1) <= 1.0 + 1e-12)
    assert out.sum() < base.sum()  # a tight target actually de-risks somewhere


def test_vol_target_live_scales_weights_and_cap():
    from ictbot.strategy.overlays.vol_target import VolTargetOverlay

    df = make_df()
    weights = {"BNB": 0.4, "ETH": 0.2}
    w2, cap2 = VolTargetOverlay(target_vol=0.002).apply_now(weights, close_df=df, cap=0.6, ctx=None)
    assert sum(w2.values()) <= sum(weights.values()) + 1e-12
    assert cap2 <= 0.6 + 1e-12


# --- ma_filter overlay ------------------------------------------------------ #


def test_ma_filter_all_above_is_identity():
    from ictbot.strategy.overlays.ma_filter import MaFilterOverlay

    # Strictly rising -> every bar above its trailing SMA -> nothing zeroed.
    n = 200
    close = np.column_stack(
        [100.0 + (1.0 + 0.1 * i) * np.arange(n) for i in range(len(CONTEST_TOKENS))]
    )
    base = np.full_like(close, 0.1)
    out = MaFilterOverlay(window=50).apply_path(base, close, p=AllocatorParams())
    assert np.array_equal(out[50:], base[50:])  # post-warmup: untouched


def test_ma_filter_all_below_goes_to_cash():
    from ictbot.strategy.overlays.ma_filter import MaFilterOverlay

    n = 200
    close = np.column_stack(
        [300.0 - (0.2 + 0.05 * i) * np.arange(n) for i in range(len(CONTEST_TOKENS))]
    )
    base = np.full_like(close, 0.1)
    out = MaFilterOverlay(window=50).apply_path(base, close, p=AllocatorParams())
    assert np.all(out[-1] == 0.0)  # falling basket -> all tokens below MA -> all cash


def test_ma_filter_contraction_invariant():
    from ictbot.strategy.overlays.ma_filter import MaFilterOverlay

    df = make_df()
    close = df.to_numpy(dtype=float)
    base = _base_path(df)
    out = MaFilterOverlay(window=50).apply_path(base, close, p=AllocatorParams())
    assert np.all(out.sum(axis=1) <= base.sum(axis=1) + 1e-12)


# --- composed REGISTERED overlay arms (momentum_voltarget / momentum_mafilter) -------------- #
# The arms above test the overlays in isolation; these pin the COMPOSED, registry-selectable arms
# end-to-end — they exist, are named, and obey the long-only de-risk invariant (may only shrink the
# book vs the momentum_adaptive base, never lever) through both weight_path and target_weights_now.

OVERLAY_ARMS = ("momentum_voltarget", "momentum_mafilter")


def test_registered_overlay_arms_exist_and_are_named():
    for name in OVERLAY_ARMS:
        assert name in registry.available()
        assert registry.get(name).name == name


def test_registered_overlay_arms_never_lever_backtest():
    df = make_df()
    close = df.to_numpy(dtype=float)
    caps = rs.cap_series(close, floor=0.40, ceiling=0.85, ma_window=50)
    base = registry.get("momentum_adaptive").weight_path(
        close, p=AllocatorParams(), cap_series=caps
    )
    for name in OVERLAY_ARMS:
        out = registry.get(name).weight_path(close, p=AllocatorParams(), cap_series=caps)
        assert np.all(out.sum(axis=1) <= base.sum(axis=1) + 1e-9)  # de-risk only
        assert np.all(out.sum(axis=1) <= 1.0 + 1e-9)


def test_registered_overlay_arms_never_lever_live():
    df = make_df()
    ctx = StratContext(
        params=AllocatorParams(), floor=0.40, ceiling=0.85, ma_window=50, fear_greed=60
    )
    base_dep = sum(
        registry.get("momentum_adaptive").target_weights_now(df, ctx=ctx).weights.values()
    )
    for name in OVERLAY_ARMS:
        d = registry.get(name).target_weights_now(df, ctx=ctx)
        assert sum(d.weights.values()) <= base_dep + 1e-9  # de-risk only
