"""Unit tests for the cross-sectional momentum allocator (the contest strategy)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy.momentum_allocator import (
    CONTEST_TOKENS,
    AllocatorParams,
    target_weights_now,
    warmup,
    weight_path,
)


def make_matrix(series: dict[str, np.ndarray]) -> pd.DataFrame:
    n = len(next(iter(series.values())))
    df = pd.DataFrame(series)
    df.insert(0, "time", pd.date_range("2024-01-01", periods=n, freq="4h"))
    return df.set_index("time")


def ramp(n, slope, base=100.0):
    return base + slope * np.arange(n)


def test_weights_respect_cap_and_topk():
    n = 200
    # 8 tokens, all rising at different rates -> all positive momentum
    cols = {t: ramp(n, slope=0.2 + 0.1 * i) for i, t in enumerate(CONTEST_TOKENS)}
    df = make_matrix(cols)
    p = AllocatorParams(top_k=2, deploy_cap=0.60)
    w = target_weights_now(df, p)
    nonzero = [v for v in w.values() if v > 0]
    assert len(nonzero) <= 2  # top_k respected
    assert abs(sum(w.values()) - 0.60) < 1e-6  # deployed exactly to the cap
    # the two strongest momenta (highest-slope tokens) are the ones held
    held = {k for k, v in w.items() if v > 0}
    assert held == {"DOT", "DOGE"}  # last two have the steepest slopes


def test_cash_filter_all_down_goes_to_usdt():
    n = 200
    cols = {t: ramp(n, slope=-0.3) for t in CONTEST_TOKENS}  # everything falling
    df = make_matrix(cols)
    w = target_weights_now(df, AllocatorParams())
    assert all(v == 0.0 for v in w.values())  # abs-momentum filter -> all USDT


def test_inverse_vol_favours_the_calmer_token():
    n = 200
    i = np.arange(n)
    calm = 100 + 0.5 * i  # smooth uptrend (low vol)
    jagged = 100 + 0.5 * i + 6.0 * np.sin(i)  # same drift, high vol
    cols = {t: ramp(n, slope=-0.3) for t in CONTEST_TOKENS}
    cols["BNB"], cols["ETH"] = calm, jagged  # only these two trend up
    df = make_matrix(cols)
    p = AllocatorParams(top_k=2, inverse_vol=True, deploy_cap=1.0)
    w = target_weights_now(df, p)
    assert w["BNB"] > 0 and w["ETH"] > 0
    assert w["BNB"] > w["ETH"]  # calmer token gets more weight


def test_live_and_backtest_paths_agree_on_last_bar():
    n = 200
    rng = np.random.default_rng(0)
    cols = {t: 100 + np.cumsum(rng.normal(0.05, 1.0, n)) for t in CONTEST_TOKENS}
    df = make_matrix(cols)
    p = AllocatorParams(rebal_bars=1)  # recompute every bar
    close = df.to_numpy()
    wp = weight_path(close, p)
    live = target_weights_now(df, p)
    for idx, t in enumerate(CONTEST_TOKENS):
        assert abs(wp[-1, idx] - live[t]) < 1e-9


def test_insufficient_history_is_all_cash():
    n = warmup(AllocatorParams()) - 5
    cols = {t: ramp(n, slope=0.3) for t in CONTEST_TOKENS}
    df = make_matrix(cols)
    w = target_weights_now(df, AllocatorParams())
    assert all(v == 0.0 for v in w.values())
