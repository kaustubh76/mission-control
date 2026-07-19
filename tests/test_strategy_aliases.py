"""Branded contest naming queue (BNB_STRATEGY_0X aliases): registration, bit-for-bit
delegation to the target arm, and selectability via the registry/selector."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.registry import CONTEST_ALIASES, StratContext


def make_df(n: int = 320) -> pd.DataFrame:
    cols = {}
    for i, t in enumerate(CONTEST_TOKENS):
        slope = (i - 3) * 0.08
        cols[t] = 100.0 + slope * np.arange(n) + 5.0 * np.sin(np.arange(n) / 10.0 + i)
    return pd.DataFrame(cols, index=pd.date_range("2024-01-01", periods=n, freq="4h"))


def test_queue_is_registered_and_resolves():
    avail = registry.available()
    for alias in CONTEST_ALIASES:
        assert alias in avail
        assert registry.get(alias) is not None
    # the specific name the user named
    assert "BNB_STRATEGY_02" in avail


def test_alias_target_mapping():
    assert registry.alias_target("BNB_STRATEGY_02") == "momentum_voltarget"
    assert registry.alias_target("BNB_STRATEGY_01") == "momentum_adaptive"
    assert registry.alias_target("momentum_adaptive") is None  # a real arm, not an alias


def test_alias_weight_path_bitwise_equals_target():
    df = make_df()
    close = df.to_numpy(dtype=float)
    from ictbot.strategy import regime_score as rs

    caps = rs.cap_series(close, floor=0.40, ceiling=0.85, ma_window=50)
    for alias, target in CONTEST_ALIASES.items():
        a, t = registry.get(alias), registry.get(target)
        for cs in (None, caps):
            assert np.array_equal(
                a.weight_path(close, p=a.default_params(), cap_series=cs),
                t.weight_path(close, p=t.default_params(), cap_series=cs),
            ), alias


def test_alias_live_decision_equals_target():
    df = make_df()
    ctx = StratContext(params=AllocatorParams(), floor=0.40, ceiling=0.85, ma_window=50)
    a = registry.get("BNB_STRATEGY_02")
    t = registry.get("momentum_voltarget")
    assert a.target_weights_now(df, ctx=ctx).weights == t.target_weights_now(df, ctx=ctx).weights


def test_alias_selectable_via_strategy_select(tmp_path, monkeypatch):
    from ictbot.runtime import strategy_select

    monkeypatch.setattr(strategy_select, "STRATEGY_SELECT_FILE", tmp_path / "strategy_select.json")
    # case-insensitive in, canonical branded name out
    assert strategy_select.save("bnb_strategy_02") == "BNB_STRATEGY_02"
    assert strategy_select.load("momentum_adaptive") == "BNB_STRATEGY_02"


def test_env_strategy_name_selects_alias_on_sim(tmp_path, monkeypatch):
    from ictbot.runtime import strategy_select

    monkeypatch.setattr(strategy_select, "STRATEGY_SELECT_FILE", tmp_path / "strategy_select.json")
    import scripts.run_allocator as ra

    monkeypatch.setattr(ra.settings, "strategy_name", "BNB_STRATEGY_02", raising=False)
    # env STRATEGY_NAME is the operator default for BOTH tracks (explicit sign-off)
    assert ra._resolve_strategy_name("sim") == "BNB_STRATEGY_02"
    assert ra._resolve_strategy_name("live") == "BNB_STRATEGY_02"
