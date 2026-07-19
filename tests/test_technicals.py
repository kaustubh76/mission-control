"""Pure technical-analysis tests (RSI/MACD/EMA + the allocator signals) — no network."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy import technicals as ta


def _series(vals):
    return np.asarray(vals, dtype=float).reshape(-1, 1)


def test_rsi_pure_uptrend_is_100():
    up = _series(np.arange(1, 60, dtype=float))  # strictly increasing → all gains
    r = ta.rsi(up).to_numpy().ravel()
    assert np.nanmax(r) == 100.0
    assert r[-1] == 100.0


def test_rsi_pure_downtrend_is_zero():
    down = _series(np.arange(60, 1, -1, dtype=float))
    r = ta.rsi(down).to_numpy().ravel()
    assert np.nanmin(r) == 0.0
    assert r[-1] == 0.0


def test_rsi_warmup_is_nan():
    x = _series(np.linspace(100, 110, 30) + np.sin(np.arange(30)))
    r = ta.rsi(x, period=14).to_numpy().ravel()
    assert np.isnan(r[:14]).all()  # first `period` rows are warmup
    assert np.isfinite(r[14:]).all()


def test_rsi_is_causal_no_lookahead():
    # Wilder/EMA indicators are causal: a prefix's values must equal the full series' prefix.
    rng = np.random.default_rng(0)
    x = _series(100 + np.cumsum(rng.standard_normal(80)))
    full = ta.rsi(x).to_numpy().ravel()
    prefix = ta.rsi(x[:50]).to_numpy().ravel()
    m = np.isfinite(full[:50]) & np.isfinite(prefix)
    assert np.allclose(full[:50][m], prefix[m])


def test_macd_histogram_identity_and_sign():
    x = _series(100 + np.cumsum(np.ones(60)))  # steady uptrend
    line, signal, hist = (d.to_numpy().ravel() for d in ta.macd(x))
    assert np.allclose(hist, line - signal, equal_nan=True)
    assert line[-1] > 0  # fast EMA above slow on an uptrend


def test_ema_causal_prefix_matches():
    x = _series(np.linspace(10, 20, 40))
    full = ta.ema(x, 12).to_numpy().ravel()
    prefix = ta.ema(x[:25], 12).to_numpy().ravel()
    assert np.allclose(full[:25], prefix)


def _matrix(trend, n=80, k=4):
    base = np.linspace(100, 100 * (1 + trend), n)
    return pd.DataFrame(np.column_stack([base * (1 + 0.001 * j) for j in range(k)]))


def test_trend_health_bounds_and_direction():
    up = ta.trend_health(_matrix(0.6))  # strong uptrend
    down = ta.trend_health(_matrix(-0.6))  # downtrend
    fin_up, fin_down = up[np.isfinite(up)], down[np.isfinite(down)]
    assert ((fin_up >= 0) & (fin_up <= 1)).all()
    assert ((fin_down >= 0) & (fin_down <= 1)).all()
    assert np.isnan(up[:5]).all()  # warmup → NaN (treated as absent)
    assert fin_up.mean() > fin_down.mean()  # healthier in an uptrend


def test_token_ta_score_bounds_shape():
    s = ta.token_ta_score(_matrix(0.4, n=70, k=5))
    assert s.shape == (70, 5)
    fin = s[np.isfinite(s)]
    assert ((fin >= 0) & (fin <= 1)).all()


def test_resample_daily_takes_last_4h_close():
    idx = pd.date_range("2026-01-01", periods=12, freq="4h", tz="UTC")  # 2 days × 6 bars
    df = pd.DataFrame({"BNB": np.arange(12, dtype=float)}, index=idx)
    daily = ta.resample_daily(df)
    assert len(daily) == 2
    assert daily["BNB"].tolist() == [5.0, 11.0]  # last 4h close of each UTC day


def test_align_daily_to_index_ffills_no_lookahead():
    daily_idx = pd.date_range("2026-01-02", periods=3, freq="1D", tz="UTC")
    vals = np.array([10.0, 20.0, 30.0])
    target = pd.date_range("2026-01-01", periods=24, freq="4h", tz="UTC")  # starts a day early
    out = ta.align_daily_to_index(vals, daily_idx, target)
    assert np.isnan(out[:6]).all()  # bars before the first daily value → NaN
    # all 6 bars of 2026-01-02 carry that day's value (10.0)
    day2 = [t for t in range(len(target)) if target[t].floor("D") == daily_idx[0]]
    assert all(out[i] == 10.0 for i in day2)
