"""
Cross-sectional momentum allocator (the committed live strategy).

WHY THIS, NOT A SIGNAL:  docs/findings.md proved the ICT entry stack has negative
out-of-sample expectancy, and a follow-up search (scripts/validate_allocator.py)
proved the same for every per-token trend signal on the 8-token universe
— net-negative basket expectancy even at 0.10% friction. There is no long-only
TA *edge* on these majors at DEX friction; a 7-day window is variance around
~breakeven, gated by a hard 30%-drawdown disqualifier.

So the shippable strategy is not an alpha signal but a RISK-CONTROLLED ALLOCATION:
each rebalance, hold the few strongest-momentum tokens, inverse-vol weighted, with
most capital parked in USDT — engineered to (a) be effectively impossible to
disqualify, (b) participate in upside when the week trends, (c) stay active enough
to clear the >=7-trade floor. This is the best point on the DQ-safe efficient
frontier (see scripts/validate_allocator.py for the rolling-7-day-window proof).

The allocator emits TARGET WEIGHTS over {tokens} (the remainder is USDT). It is a
portfolio rebalancer, not a bracket trader — there are no per-trade SL/TP stops;
risk is controlled by the deployment cap + the cash filter + diversification.

Two entry points, kept bit-for-bit consistent by tests/test_momentum_allocator.py:
  - target_weights_now(close_df, p)  -> dict[token, weight]   (live path)
  - weight_path(close, p)            -> ndarray[n, k]         (vectorised backtest)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Contest BEP-20 universe + BNB. Order is the canonical column order everywhere.
CONTEST_TOKENS = ("BNB", "ETH", "CAKE", "LINK", "UNI", "AVAX", "DOT", "DOGE")


@dataclass(frozen=True)
class AllocatorParams:
    lookback: int = 120  # momentum ranking horizon (4h bars)
    top_k: int = 2  # how many tokens to hold
    deploy_cap: float = 0.60  # max fraction of NAV deployed (rest = USDT) — the risk dial
    inverse_vol: bool = True  # size the held tokens by 1/vol (else equal weight)
    vol_lookback: int = 30  # bars used to estimate vol for inverse-vol sizing
    rebal_bars: int = 6  # rebalance cadence (6 x 4h = daily)
    abs_filter: bool = True  # only hold tokens whose trailing return > 0 (cash filter)
    # Min per-bar return std for inverse-vol sizing. 0.0 = legacy (no floor) — a strict
    # no-op since the vol line already coalesces 0 -> 1e-9, so max(std, 0.0) is identical.
    # The CMC arm sets a positive floor so that during the cold-start window (CMC daily
    # closes forward-filled flat across six 4h slots → 5-of-6 returns are 0 → std collapses
    # → 1/vol blows up) the inverse-vol split reflects real DAILY vol, not seed artifacts.
    vol_floor: float = 0.0


def cmc_seed_vol_floor(close_df: pd.DataFrame) -> float:
    """Per-tick inverse-vol FLOOR derived from CMC's own DAILY vol — the cold-start seed protection
    shared by EVERY arm that runs on the cmc_4h feed (injected in run_allocator when candle_source is
    'cmc_4h'; the momentum_cmc adapter also delegates here).

    The cold-start seed forward-fills each CMC daily close across the six 4h slots of its day, so
    5-of-6 intrabar returns are exactly 0. The 30-bar 4h vol then collapses and 1/vol blows up,
    misweighting the held tokens on a seed artifact. We floor each token's vol at the cross-token
    MEDIAN of the real DAILY return-std, rescaled from the daily to the 4h-bar scale by 1/√6 (a daily
    return ≈ Σ of six 4h returns → std scales with √6). During the seed window every token's 4h-std
    sits below this floor → they clamp equal → equal-weight (the honest "no real intrabar vol yet"
    stance); once real 4h bars accrue (well before the contest, vol_lookback=30 bars = 5 days) the
    true intrabar std rises above the floor and the real inverse-vol tilt returns. Degrades to 0.0
    (legacy, no floor) on any thin/degenerate input. Lazy import avoids a strategy<->technicals cycle."""
    try:
        from ictbot.strategy.technicals import resample_daily

        daily = resample_daily(close_df)
        dstd = daily.pct_change().tail(30).std()
        med = float(dstd.median())
    except Exception:
        return 0.0
    if not (med > 0) or med != med:  # non-positive or NaN
        return 0.0
    return med / (6.0**0.5)


def warmup(p: AllocatorParams) -> int:
    return max(p.lookback, p.vol_lookback) + 1


def _weights_at(
    close: np.ndarray, rets: np.ndarray, i: int, p: AllocatorParams, cap: float | None = None
) -> np.ndarray:
    """Target weight vector at bar i (held into i+1). Sums to <= the deployment cap.

    `cap` overrides `p.deploy_cap` when given (the regime-adaptive path supplies a
    live cap here); `cap=None` reproduces the static behaviour bit-for-bit.
    """
    k = close.shape[1]
    w = np.zeros(k)
    if i < warmup(p):
        return w
    eff_cap = p.deploy_cap if cap is None else float(cap)
    trailing = close[i] / close[i - p.lookback] - 1.0
    order = np.argsort(trailing)[::-1]
    picks = [j for j in order if (trailing[j] > 0 or not p.abs_filter)][: p.top_k]
    if not picks:
        return w  # nothing trends up -> all USDT
    if p.inverse_vol:
        vol = np.array(
            [max(rets[i - p.vol_lookback + 1 : i + 1, j].std(), p.vol_floor) or 1e-9 for j in picks]
        )
        raw = 1.0 / vol
        ww = raw / raw.sum()
    else:
        ww = np.ones(len(picks)) / len(picks)
    for j, weight in zip(picks, ww, strict=False):
        w[j] = weight
    return w * eff_cap  # scale the (sum=1) book down to the cap


def weight_path(
    close: np.ndarray, p: AllocatorParams, cap_series: np.ndarray | None = None
) -> np.ndarray:
    """Vectorised-ish backtest path: weights recomputed every `rebal_bars`,
    held flat in between. `close` is an (n, k) matrix in CONTEST_TOKENS order.

    `cap_series` (length n) supplies a per-bar deployment cap for the regime-
    adaptive backtest; `None` uses the static `p.deploy_cap` (unchanged behaviour).
    """
    n, k = close.shape
    rets = np.vstack([np.zeros(k), close[1:] / close[:-1] - 1.0])
    w = np.zeros((n, k))
    cur = np.zeros(k)
    for i in range(n):
        if i % p.rebal_bars == 0:
            cap = None if cap_series is None else float(cap_series[i])
            cur = _weights_at(close, rets, i, p, cap=cap)
        w[i] = cur
    return w


def weight_path_tilted(
    close: np.ndarray,
    p: AllocatorParams,
    cap_series: np.ndarray | None = None,
    *,
    tilt_lo: float = 0.85,
    tilt_hi: float = 1.15,
    tilt_lookback: int = 42,
    tokens: tuple[str, ...] = CONTEST_TOKENS,
) -> np.ndarray:
    """Like `weight_path`, but at each rebalance the held weights are tilted by
    `universe_overlay.momentum_tilt` using the candle 7-day return
    (`close[i]/close[i-tilt_lookback]-1`, ×100) as the `pct_7d` proxy — the backtestable
    stand-in for CMC's `percent_change_7d`. The tilt re-normalizes to the SAME total, so
    deployment + cash are preserved; only the split among held tokens shifts.

    `tokens` must match `close`'s column order (pass `tuple(mat.columns)` when
    `align_close_matrix` dropped a token; defaults to the full CONTEST_TOKENS)."""
    from ictbot.strategy.universe_overlay import momentum_tilt

    n, k = close.shape
    rets = np.vstack([np.zeros(k), close[1:] / close[:-1] - 1.0])
    w = np.zeros((n, k))
    cur = np.zeros(k)
    for i in range(n):
        if i % p.rebal_bars == 0:
            cap = None if cap_series is None else float(cap_series[i])
            row = _weights_at(close, rets, i, p, cap=cap)
            if i >= tilt_lookback:
                held = {tokens[j]: float(row[j]) for j in range(k) if row[j] > 0}
                if len(held) > 1:
                    pct = {
                        tokens[j]: {
                            "pct_7d": float(close[i, j] / close[i - tilt_lookback, j] - 1.0) * 100.0
                        }
                        for j in range(k)
                    }
                    tilted = momentum_tilt(held, pct, lo=tilt_lo, hi=tilt_hi)
                    row = np.array([tilted.get(tokens[j], 0.0) for j in range(k)], dtype=float)
            cur = row
        w[i] = cur
    return w


def _weights_at_ranked(
    close: np.ndarray,
    rets: np.ndarray,
    i: int,
    p: AllocatorParams,
    cap: float | None = None,
    blend: dict[int, float] | None = None,
    ta_score: np.ndarray | None = None,
    w_ta_rank: float = 0.0,
) -> np.ndarray:
    """Like `_weights_at`, but RANKS by a multi-timeframe blended momentum score
    (weighted cross-sectional z-score of returns over each lookback in `blend`) instead
    of a single trailing return. `blend={p.lookback: 1.0}` (the default) is bit-for-bit
    the baseline: z-score is monotonic in the return, so the top-k ordering is unchanged.
    The cash filter (abs_filter) still uses the PRIMARY `p.lookback` return.

    With `ta_score` (per-bar (n,k) CMC TA confirmation in [0,1]) + `w_ta_rank>0`, the
    ranking key adds `w_ta_rank*(ta-0.5)` — TA-confirmed momentum (boosts positive-MACD /
    healthy-RSI names, penalises overbought). `ta_score=None` or `w_ta_rank=0` is identical."""
    k = close.shape[1]
    w = np.zeros(k)
    if i < warmup(p):
        return w
    eff_cap = p.deploy_cap if cap is None else float(cap)
    blend = blend or {p.lookback: 1.0}
    ret_primary = close[i] / close[i - p.lookback] - 1.0
    score = np.zeros(k)
    tot = 0.0
    for lb, bw in blend.items():
        if bw == 0 or i < lb:
            continue
        r = close[i] / close[i - lb] - 1.0
        sd = r.std() or 1e-9
        score += bw * (r - r.mean()) / sd  # cross-sectional z (scale-invariant)
        tot += bw
    if tot <= 0:
        score = ret_primary  # degenerate blend → primary momentum
    rank_key = score
    if ta_score is not None and w_ta_rank > 0:  # CMC TA confirmation tilt on the ranking
        ta_row = ta_score[i]
        ta_adj = np.where(np.isfinite(ta_row), ta_row - 0.5, 0.0)
        rank_key = score + w_ta_rank * ta_adj
    cand = [j for j in range(k) if (ret_primary[j] > 0 or not p.abs_filter)]
    cand.sort(key=lambda j: rank_key[j], reverse=True)
    picks = cand[: p.top_k]
    if not picks:
        return w
    if p.inverse_vol:
        vol = np.array(
            [max(rets[i - p.vol_lookback + 1 : i + 1, j].std(), p.vol_floor) or 1e-9 for j in picks]
        )
        ww = (1.0 / vol) / (1.0 / vol).sum()
    else:
        ww = np.ones(len(picks)) / len(picks)
    for j, weight in zip(picks, ww, strict=False):
        w[j] = weight
    return w * eff_cap


def weight_path_ranked(
    close: np.ndarray,
    p: AllocatorParams,
    cap_series: np.ndarray | None = None,
    *,
    blend: dict[int, float] | None = None,
    tilt: bool = False,
    tilt_lo: float = 0.85,
    tilt_hi: float = 1.15,
    tilt_lookback: int = 42,
    ta_score: np.ndarray | None = None,
    w_ta_rank: float = 0.0,
    tokens: tuple[str, ...] = CONTEST_TOKENS,
) -> np.ndarray:
    """`weight_path` using the CMC-driven multi-timeframe blended ranking (the 'go
    deeper' arm) + optional within-set tilt + optional CMC TA confirmation (`ta_score`,
    `w_ta_rank`). `blend={p.lookback: 1.0}` with `w_ta_rank=0` reduces bit-for-bit to the
    single-lookback baseline. `tokens` must match `close`'s column order."""
    from ictbot.strategy.universe_overlay import momentum_tilt

    n, k = close.shape
    rets = np.vstack([np.zeros(k), close[1:] / close[:-1] - 1.0])
    blend = blend or {p.lookback: 1.0}
    w = np.zeros((n, k))
    cur = np.zeros(k)
    for i in range(n):
        if i % p.rebal_bars == 0:
            cap = None if cap_series is None else float(cap_series[i])
            row = _weights_at_ranked(
                close, rets, i, p, cap=cap, blend=blend, ta_score=ta_score, w_ta_rank=w_ta_rank
            )
            if tilt and i >= tilt_lookback:
                held = {tokens[j]: float(row[j]) for j in range(k) if row[j] > 0}
                if len(held) > 1:
                    pct = {
                        tokens[j]: {
                            "pct_7d": float(close[i, j] / close[i - tilt_lookback, j] - 1.0) * 100.0
                        }
                        for j in range(k)
                    }
                    tilted = momentum_tilt(held, pct, lo=tilt_lo, hi=tilt_hi)
                    row = np.array([tilted.get(tokens[j], 0.0) for j in range(k)], dtype=float)
            cur = row
        w[i] = cur
    return w


def target_weights_now(
    close_df: pd.DataFrame, p: AllocatorParams | None = None
) -> dict[str, float]:
    """Live path: target weights from the LAST row of an aligned close frame.

    `close_df` columns are token symbols (any subset/superset of CONTEST_TOKENS);
    only known tokens are used and the result is keyed by symbol. The remainder of
    NAV (1 - sum(weights)) is held in USDT by the caller.
    """
    p = p or AllocatorParams()
    cols = [c for c in close_df.columns if c in CONTEST_TOKENS]
    sub = close_df[cols].dropna()
    if len(sub) < warmup(p):
        return {c: 0.0 for c in cols}  # insufficient history -> all USDT
    close = sub.to_numpy(dtype=float)
    rets = np.vstack([np.zeros(close.shape[1]), close[1:] / close[:-1] - 1.0])
    w = _weights_at(close, rets, len(sub) - 1, p)
    return {c: float(w[idx]) for idx, c in enumerate(cols)}
