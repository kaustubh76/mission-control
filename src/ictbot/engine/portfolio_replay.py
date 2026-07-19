"""
Portfolio-level backtest for the momentum allocator (the contest-correct harness).

The single-asset wfo_replay scores one position at a time with SL/TP brackets —
it cannot evaluate an ALLOCATION strategy whose risk control is the deployment
cap + cash filter + diversification (no stops). This module simulates a target-
weight book: each bar it earns the weighted constituent return and pays friction
on TURNOVER (sum of |Δweight| × one-way cost — a sell or a buy on each changed
unit of weight), then reports the rolling 7-day (42-bar) return/drawdown
distribution that actually models 'a random contest week'.

Friction one-way ≈ fee + slippage per side; 0.0015 ≈ 0.30% round-trip (v3 majors),
0.0035 ≈ 0.70% round-trip (v2 / pegged BEP-20).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BARS_PER_WEEK = 42  # 7 days of 4h bars — the contest window length
ONE_WAY_30BPS = 0.0015
ONE_WAY_70BPS = 0.0035


def returns_matrix(close: np.ndarray) -> np.ndarray:
    return np.vstack([np.zeros(close.shape[1]), close[1:] / close[:-1] - 1.0])


def simulate(weight_path: np.ndarray, rets: np.ndarray, one_way: float) -> tuple[np.ndarray, int]:
    """Run a target-weight book. weight_path[i] is held over bar i-1 -> i.

    Returns (equity_curve, n_transactions). Friction is charged on the weight
    change at each bar; cash (1 - sum w) earns 0.
    """
    n, k = rets.shape
    eq = np.ones(n)
    w_prev = np.zeros(k)
    txns = 0
    for i in range(1, n):
        w = weight_path[i]
        dw = np.abs(w - w_prev)
        fric = float(dw.sum()) * one_way
        txns += int((dw > 1e-6).sum())
        port_ret = float((w_prev * rets[i]).sum())
        eq[i] = eq[i - 1] * (1.0 + port_ret) * (1.0 - fric)
        w_prev = w
    return eq, txns


def curve_metrics(eq: np.ndarray) -> dict:
    total = eq[-1] / eq[0] - 1.0
    peak = np.maximum.accumulate(eq)
    max_dd = float(np.max((peak - eq) / peak)) if eq.size else 0.0
    return {"total_return": float(total), "max_dd": max_dd}


def rolling_window_stats(
    eq: np.ndarray, warmup: int, win: int = BARS_PER_WEEK, start_mask: np.ndarray | None = None
) -> dict:
    """Distribution of `win`-bar return + intra-window drawdown across all windows.

    This is the contest-relevant view: every window is a hypothetical contest run.
    `start_mask` (bool array, len == len(eq)) restricts to windows whose START bar
    is True — used to condition the distribution on the regime at entry.
    """
    rets, dds = [], []
    for a in range(warmup, len(eq) - win):
        if start_mask is not None and not start_mask[a]:
            continue
        seg = eq[a : a + win + 1]
        if seg[0] <= 0:
            continue
        rets.append(seg[-1] / seg[0] - 1.0)
        peak = np.maximum.accumulate(seg)
        dds.append(float(np.max((peak - seg) / peak)))
    if not rets:
        return {"n_windows": 0}
    rets, dds = np.asarray(rets), np.asarray(dds)
    return {
        "n_windows": int(rets.size),
        "median_ret": float(np.median(rets)),
        "mean_ret": float(np.mean(rets)),
        "p5_ret": float(np.percentile(rets, 5)),
        "p95_ret": float(np.percentile(rets, 95)),
        "pct_up": float((rets > 0).mean()),
        "worst_week_dd": float(np.max(dds)),
        "median_week_dd": float(np.median(dds)),
        "pct_dd_over_15": float((dds > 0.15).mean()),
        "pct_dd_over_30": float((dds > 0.30).mean()),  # the disqualification gate
    }


def evaluate(
    close: np.ndarray,
    weight_path: np.ndarray,
    *,
    one_way: float = ONE_WAY_30BPS,
    warmup: int = 160,
) -> dict:
    """Convenience: simulate a weight path + return rolling-window stats + trades/wk."""
    rets = returns_matrix(close)
    eq, txns = simulate(weight_path, rets, one_way)
    stats = rolling_window_stats(eq, warmup=warmup)
    n_eff = len(rets) - 1
    stats["trades_per_week"] = txns * BARS_PER_WEEK / n_eff if n_eff else 0.0
    stats.update(curve_metrics(eq))
    return stats


def slice_by_date(close_df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    """Restrict an aligned close matrix (index = time) to [start, end] ISO dates."""
    out = close_df
    if start is not None:
        out = out[out.index >= pd.Timestamp(start, tz=out.index.tz)]
    if end is not None:
        out = out[out.index <= pd.Timestamp(end, tz=out.index.tz)]
    return out


def align_close_matrix(frames: dict[str, pd.DataFrame], tokens) -> pd.DataFrame:
    """Inner-join per-token OHLCV frames on `time` into a close matrix in `tokens`
    column order (drops tokens without enough overlap)."""
    cols = {}
    for t in tokens:
        df = frames.get(t)
        if df is None or len(df) < 200:
            continue
        cols[t] = df.set_index("time")["close"].astype(float)
    mat = pd.DataFrame(cols).dropna()
    return mat
