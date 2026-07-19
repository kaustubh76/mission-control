"""
Regime-adaptive deployment for the momentum allocator.

THE PROBLEM THIS SOLVES: a backtest only describes events that already happened.
Freezing a deployment cap to one (net-bearish) historical window is hindsight bias.
Instead the agent scales how much capital it deploys by a LIVE, forward-available
**risk-on score** — deploying more when the basket is actually trending up, pulling
to cash when it isn't. The contest week's regime is unknown; this lets the agent
react to whatever it turns out to be.

DESIGN PRINCIPLES (deliberate, to avoid re-introducing the bias we're fighting):
  - **Principled, not fitted.** The score is simple linear maps of breadth / trend /
    vol — NOT grid-searched on the sample. The forward paper run is the real arbiter.
  - **Close-only, so backtest == live.** Every term is computed from the close matrix
    (or the equal-weight index built from it), so the vectorised backtest models the
    EXACT live decision rule. (The OHLC indicators in `indicators/regime.py` /
    `bias_slope.py` are the conceptual basis; we mirror their logic close-only for
    consistency.) Fear & Greed is the ONE live-only enhancer — it has no offline
    history, so the backtest score omits it and live folds it in. Documented, not hidden.

Score ∈ [0,1] → deployment cap ∈ [floor, ceiling] (the participatory band 0.40–0.85).
The per-token absolute-momentum cash filter still applies on top: if nothing trends
up, the book is all-USDT regardless of the cap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ictbot.strategy.momentum_allocator import (
    CONTEST_TOKENS,
    AllocatorParams,
    _weights_at,
    _weights_at_ranked,
    warmup,
)


# --------------------------------------------------------------------------- #
# Enhanced-regime intel (CMC Startup tier) — LIVE-ONLY, like Fear & Greed.
# These terms have no offline history, so the deterministic backtest path omits
# them entirely (intel=None) and they are bit-for-bit identical to the validated
# model; LIVE folds them in only when CMC_REGIME_ENHANCED is on.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RegimeIntel:
    btc_dominance: float | None = None  # % now
    btc_dominance_prev: float | None = None  # % ~30d ago
    total_mktcap: float | None = None  # USD now
    total_mktcap_prev: float | None = None  # USD ~30d ago
    fng_now: int | None = None
    fng_7d_avg: float | None = None
    # Relative influence of each term in the score mean (0 disables a term).
    w_dominance: float = 1.0
    w_mktcap: float = 1.0
    w_fng_mom: float = 1.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _dominance_term(ri: RegimeIntel) -> float | None:
    """Falling BTC dominance = alts outperforming = risk-on for the alt-heavy basket."""
    if ri.btc_dominance is None or not ri.btc_dominance_prev:
        return None
    chg = (ri.btc_dominance_prev - ri.btc_dominance) / ri.btc_dominance_prev
    return _clamp01(0.5 + 3.0 * chg)


def _mktcap_term(ri: RegimeIntel) -> float | None:
    """Expanding total market cap = macro risk-on; contracting = risk-off."""
    if ri.total_mktcap is None or not ri.total_mktcap_prev:
        return None
    chg = ri.total_mktcap / ri.total_mktcap_prev - 1.0
    return _clamp01(0.5 + 2.0 * chg)


def _fng_mom_term(ri: RegimeIntel) -> float | None:
    """Improving sentiment (F&G above its 7d average) > the absolute level alone."""
    if ri.fng_now is None or ri.fng_7d_avg is None:
        return None
    return _clamp01(0.5 + (ri.fng_now - ri.fng_7d_avg) / 50.0)


def index_series(close: np.ndarray) -> np.ndarray:
    """Equal-weight, base-normalised basket index (a single price series)."""
    base = close[0]
    base = np.where(base == 0, 1.0, base)
    return (close / base).mean(axis=1)


def _breadth(close: np.ndarray, i: int, ma_window: int) -> float:
    """Fraction of tokens trading above their `ma_window` SMA at bar i."""
    if i < ma_window:
        return 0.0
    sma = close[i - ma_window + 1 : i + 1].mean(axis=0)
    return float((close[i] > sma).mean())


def _trend(idx: np.ndarray, i: int, fast: int, slow: int) -> float:
    """1.0 if the basket index fast-SMA > slow-SMA (uptrend), else 0.0."""
    if i < slow:
        return 0.0
    fast_sma = idx[i - fast + 1 : i + 1].mean()
    slow_sma = idx[i - slow + 1 : i + 1].mean()
    return 1.0 if fast_sma > slow_sma else 0.0


def _vol_factor(
    idx: np.ndarray, i: int, win: int = 200, hi: float = 0.70, lo: float = 0.30
) -> float:
    """Volatility brake ∈ [0.6, 1.0]: HIGH_VOL (ECDF rank ≥ hi) cuts deployment to
    0.6, LOW/NORMAL leaves it at 1.0 — mirrors atr_percentile_regime, close-only."""
    if i < win + 1:
        return 1.0
    rets = np.abs(np.diff(idx[i - win : i + 1]))  # |returns| as the vol proxy
    cur = rets[-1] if rets.size else 0.0
    if cur <= 0:
        return 1.0
    rank = float((rets <= cur).mean())
    if rank >= hi:
        return 0.6  # high vol -> deploy less
    return 1.0


def regime_score(
    close: np.ndarray,
    i: int,
    *,
    ma_window: int = 50,
    fast: int = 20,
    slow: int = 50,
    vol_win: int = 200,
    fear_greed: int | None = None,
    intel: RegimeIntel | None = None,
    ta_health: float | None = None,
    w_ta: float = 1.0,
) -> float:
    """Risk-on score ∈ [0,1] at bar i. Higher = more of the basket trending up,
    calmer vol, (live) greedier sentiment → deploy more.

    The score is a WEIGHTED mean of available terms. With `intel=None` (the offline
    backtest path) the only terms are breadth + trend (+ F&G if supplied), all weight
    1.0 → an unweighted mean, BIT-FOR-BIT identical to the validated model. When
    `intel` is supplied (LIVE, CMC_REGIME_ENHANCED on) the dominance / mktcap / F&G-
    momentum terms are folded in at their configured weights."""
    idx = index_series(close)
    pairs: list[tuple[float, float]] = [
        (_breadth(close, i, ma_window), 1.0),
        (_trend(idx, i, fast, slow), 1.0),
    ]
    if fear_greed is not None:  # live-only enhancer
        pairs.append((_clamp01(fear_greed / 100.0), 1.0))
    if intel is not None:  # CMC Startup-tier terms
        for val, w in (
            (_dominance_term(intel), intel.w_dominance),
            (_mktcap_term(intel), intel.w_mktcap),
            (_fng_mom_term(intel), intel.w_fng_mom),
        ):
            if val is not None and w > 0:
                pairs.append((val, w))
    if ta_health is not None and w_ta > 0:  # CMC TA trend-health term
        pairs.append((_clamp01(ta_health), w_ta))
    wsum = sum(w for _, w in pairs)
    base = sum(v * w for v, w in pairs) / wsum if wsum else 0.0
    return base * _vol_factor(idx, i, vol_win)


def regime_labels(
    close: np.ndarray, *, fast: int = 20, slow: int = 50, vol_win: int = 200, hi: float = 0.70
) -> np.ndarray:
    """Per-bar regime label for conditioning the backtest: BULL / BEAR / CHOP.

    BULL = basket index up-trend (fast-SMA > slow-SMA) and not high-vol.
    CHOP = high-vol (ECDF rank ≥ hi) regardless of trend.
    BEAR = down-trend, normal vol. Bars before `slow` are 'WARMUP'.
    """
    idx = index_series(close)
    n = close.shape[0]
    out = np.array(["WARMUP"] * n, dtype=object)
    for i in range(slow, n):
        if _vol_factor(idx, i, vol_win, hi=hi) < 1.0:
            out[i] = "CHOP"
        elif _trend(idx, i, fast, slow) > 0:
            out[i] = "BULL"
        else:
            out[i] = "BEAR"
    return out


def adaptive_cap(score: float, floor: float, ceiling: float) -> float:
    """Map a risk-on score ∈ [0,1] linearly into the deployment band [floor, ceiling]."""
    return float(floor + max(0.0, min(1.0, score)) * (ceiling - floor))


def cap_series(
    close: np.ndarray,
    *,
    floor: float,
    ceiling: float,
    ma_window: int = 50,
    fast: int = 20,
    slow: int = 50,
    vol_win: int = 200,
) -> np.ndarray:
    """Per-bar deployment cap for the regime-adaptive BACKTEST (no F&G — offline)."""
    n = close.shape[0]
    out = np.full(n, floor, dtype=float)
    for i in range(n):
        s = regime_score(close, i, ma_window=ma_window, fast=fast, slow=slow, vol_win=vol_win)
        out[i] = adaptive_cap(s, floor, ceiling)
    return out


def cap_series_enhanced(
    close: np.ndarray,
    *,
    floor: float,
    ceiling: float,
    dominance: np.ndarray | None = None,
    dominance_prev: np.ndarray | None = None,
    mktcap: np.ndarray | None = None,
    mktcap_prev: np.ndarray | None = None,
    fng: np.ndarray | None = None,
    fng_7d: np.ndarray | None = None,
    ta_health: np.ndarray | None = None,
    w_dominance: float = 1.0,
    w_mktcap: float = 1.0,
    w_fng_mom: float = 1.0,
    w_ta: float = 1.0,
    ma_window: int = 50,
    fast: int = 20,
    slow: int = 50,
    vol_win: int = 200,
) -> np.ndarray:
    """Per-bar deployment cap WITH the CMC Startup-tier macro terms folded in — the
    BACKTESTABLE counterpart to the LIVE `adaptive_target_weights(intel=...)` path, so
    the enhanced regime can finally be A/B'd on PnL.

    Pass per-bar aligned macro arrays (see `macro_align.align_macro_to_index`). Each bar
    builds a `RegimeIntel` and reuses the EXISTING `regime_score(..., intel=, fear_greed=)`
    + `adaptive_cap`. A per-bar term that is NaN/None self-disables (regime_score drops
    None terms). With EVERY macro array None this is **bit-for-bit identical** to
    `cap_series(close, floor=, ceiling=, ma_window=, fast=, slow=, vol_win=)` — the
    regression that protects the validated contest path."""
    n = close.shape[0]
    out = np.full(n, floor, dtype=float)
    have_macro = any(
        a is not None for a in (dominance, dominance_prev, mktcap, mktcap_prev, fng, fng_7d)
    )

    def _at(a: np.ndarray | None, i: int) -> float | None:
        if a is None:
            return None
        v = a[i]
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)

    for i in range(n):
        intel = None
        fg: int | None = None
        if have_macro:
            fi = _at(fng, i)
            fg = int(round(fi)) if fi is not None else None
            intel = RegimeIntel(
                btc_dominance=_at(dominance, i),
                btc_dominance_prev=_at(dominance_prev, i),
                total_mktcap=_at(mktcap, i),
                total_mktcap_prev=_at(mktcap_prev, i),
                fng_now=fg,
                fng_7d_avg=_at(fng_7d, i),
                w_dominance=w_dominance,
                w_mktcap=w_mktcap,
                w_fng_mom=w_fng_mom,
            )
        s = regime_score(
            close,
            i,
            ma_window=ma_window,
            fast=fast,
            slow=slow,
            vol_win=vol_win,
            fear_greed=fg,
            intel=intel,
            ta_health=_at(ta_health, i),
            w_ta=w_ta,
        )
        out[i] = adaptive_cap(s, floor, ceiling)
    return out


def adaptive_target_weights(
    close_df: pd.DataFrame,
    p: AllocatorParams,
    *,
    floor: float,
    ceiling: float,
    ma_window: int = 50,
    fear_greed: int | None = None,
    intel: RegimeIntel | None = None,
    ta_health: float | None = None,
    w_ta: float = 1.0,
    ta_token_scores: dict[str, float] | None = None,
    w_ta_rank: float = 0.0,
    active: list[str] | tuple[str, ...] | None = None,
) -> tuple[dict[str, float], float, float]:
    """LIVE path: target weights with a regime-adaptive cap from the LAST bar.

    Returns (weights_by_token, regime_score, chosen_cap) so the runtime can journal
    the regime + cap it acted on. Falls back to all-USDT on insufficient history.
    `intel` (None by default) adds the CMC Startup-tier terms when CMC_REGIME_ENHANCED
    is on; `ta_health` (None) adds the CMC TA trend-health term to the cap when
    ALLOC_TA_ENABLED is on; `ta_token_scores` (None) + `w_ta_rank>0` add CMC TA
    CONFIRMATION to the token ranking. With all None/0 the decision is identical to the
    validated model (the ranking stays on `_weights_at`).

    `active` (UI token toggles) restricts the RANKING universe — top-k is picked
    only from these columns; everything else gets weight 0.0. The regime score /
    breadth above stays FULL-universe (it's a market gauge, not a portfolio gauge).
    `active=None` (or covering every column) is bit-for-bit the legacy path.
    """
    cols = [c for c in close_df.columns if c in CONTEST_TOKENS]
    sub = close_df[cols].dropna()
    if len(sub) < max(warmup(p), ma_window + 1):
        return {c: 0.0 for c in cols}, 0.0, floor
    close = sub.to_numpy(dtype=float)
    rets = np.vstack([np.zeros(close.shape[1]), close[1:] / close[:-1] - 1.0])
    i = len(sub) - 1
    score = regime_score(
        close,
        i,
        ma_window=ma_window,
        fear_greed=fear_greed,
        intel=intel,
        ta_health=ta_health,
        w_ta=w_ta,
    )
    cap = adaptive_cap(score, floor, ceiling)
    # An empty/None active set never silently zeroes the book — degrade to full universe
    # (active_tokens.load() already guarantees >= 2, this is defence in depth).
    rank_cols = cols if not active else [c for c in cols if c in set(active)] or cols
    if rank_cols == cols:
        if ta_token_scores and w_ta_rank > 0:
            # CMC TA-confirmed ranking. `_weights_at_ranked(blend={lookback:1})` is bit-for-bit
            # `_weights_at` at w_ta_rank=0, so this only changes the order when TA actually tilts.
            ta_mat = np.full((len(sub), len(cols)), np.nan)
            ta_mat[i] = [ta_token_scores.get(c, np.nan) for c in cols]
            w = _weights_at_ranked(
                close,
                rets,
                i,
                p,
                cap=cap,
                blend={p.lookback: 1.0},
                ta_score=ta_mat,
                w_ta_rank=w_ta_rank,
            )
        else:
            w = _weights_at(close, rets, i, p, cap=cap)
        return {c: float(w[idx]) for idx, c in enumerate(cols)}, score, cap
    # Restricted ranking: same rows (sub is already aligned), active columns only.
    sub_r = sub[rank_cols]
    close_r = sub_r.to_numpy(dtype=float)
    rets_r = np.vstack([np.zeros(close_r.shape[1]), close_r[1:] / close_r[:-1] - 1.0])
    if ta_token_scores and w_ta_rank > 0:
        ta_mat = np.full((len(sub_r), len(rank_cols)), np.nan)
        ta_mat[i] = [ta_token_scores.get(c, np.nan) for c in rank_cols]
        w = _weights_at_ranked(
            close_r,
            rets_r,
            i,
            p,
            cap=cap,
            blend={p.lookback: 1.0},
            ta_score=ta_mat,
            w_ta_rank=w_ta_rank,
        )
    else:
        w = _weights_at(close_r, rets_r, i, p, cap=cap)
    weights = {c: 0.0 for c in cols}
    weights.update({c: float(w[j]) for j, c in enumerate(rank_cols)})
    return weights, score, cap


def regime_breakdown(
    close_df: pd.DataFrame,
    *,
    ma_window: int = 50,
    fast: int = 20,
    slow: int = 50,
    vol_win: int = 200,
    fear_greed: int | None = None,
    intel: RegimeIntel | None = None,
) -> dict:
    """Per-term contributions at the LAST bar — for journaling + the dashboard. Pure
    read (does not affect the decision). Returns {} on insufficient history; absent
    terms (offline/unavailable) are None."""
    cols = [c for c in close_df.columns if c in CONTEST_TOKENS]
    sub = close_df[cols].dropna()
    if len(sub) < max(2, ma_window + 1):
        return {}
    close = sub.to_numpy(dtype=float)
    idx = index_series(close)
    i = len(sub) - 1
    out: dict = {
        "breadth": round(_breadth(close, i, ma_window), 3),
        "trend": _trend(idx, i, fast, slow),
        "vol_factor": round(_vol_factor(idx, i, vol_win), 3),
        "fng": round(_clamp01(fear_greed / 100.0), 3) if fear_greed is not None else None,
    }
    if intel is not None:
        for name, val in (
            ("dominance", _dominance_term(intel)),
            ("mktcap", _mktcap_term(intel)),
            ("fng_mom", _fng_mom_term(intel)),
        ):
            out[name] = round(val, 3) if val is not None else None
    out["score"] = round(
        regime_score(
            close,
            i,
            ma_window=ma_window,
            fast=fast,
            slow=slow,
            vol_win=vol_win,
            fear_greed=fear_greed,
            intel=intel,
        ),
        3,
    )
    return out
