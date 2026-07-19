"""
CMC universe-tilt tests — the tilt re-weights WITHIN the held set but must preserve the
total deployment (so the regime-chosen cap + cash level are untouched) and stay bounded.
"""

from __future__ import annotations

import numpy as np

from ictbot.strategy.momentum_allocator import (
    AllocatorParams,
    weight_path,
    weight_path_ranked,
    weight_path_tilted,
)
from ictbot.strategy.universe_overlay import (
    cmc_momentum_tilt,
    momentum_tilt,
    sector_tilt,
)


def _trend_close(n: int = 400, k: int = 8):
    """k up-trending tokens with DIFFERENT slopes → top-2 differ in 7d return (tiltable)."""
    base = np.arange(n)[:, None].astype(float)
    slopes = (np.arange(k) + 1) * 0.1
    return 100.0 + base * slopes[None, :]


def test_tilt_preserves_total_deployment():
    w = {"BNB": 0.3, "ETH": 0.2}
    tc = {"BNB": {"pct_7d": 10.0}, "ETH": {"pct_7d": -5.0}}
    t = momentum_tilt(w, tc)
    assert abs(sum(t.values()) - sum(w.values())) < 1e-9


def test_tilt_favors_stronger_token():
    w = {"BNB": 0.25, "ETH": 0.25}
    tc = {"BNB": {"pct_7d": 10.0}, "ETH": {"pct_7d": -10.0}}
    t = momentum_tilt(w, tc)
    assert t["BNB"] > t["ETH"]


def test_tilt_is_bounded():
    w = {"A": 0.5, "B": 0.5}
    tc = {"A": {"pct_7d": 1000.0}, "B": {"pct_7d": -1000.0}}
    t = momentum_tilt(w, tc)
    assert max(t.values()) / min(t.values()) <= (1.15 / 0.85) + 1e-9


def test_tilt_honors_asymmetric_lo_band():
    # CMC-4: an asymmetric band (lo above the raw floor) binds the weaker leg via the
    # max(lo, ·) clamp — lifting its share vs the symmetric default (clamp inert), with
    # total deployment preserved either way.
    w = {"A": 0.5, "B": 0.5}
    tc = {"A": {"pct_7d": 1000.0}, "B": {"pct_7d": -1000.0}}  # A strong, B weak
    sym = momentum_tilt(w, tc, lo=0.85, hi=1.15)  # symmetric default: clamp inert
    asym = momentum_tilt(w, tc, lo=0.95, hi=1.15)  # lo=0.95 floors the weak leg
    assert asym["B"] > sym["B"]  # the lo clamp lifted B's share
    assert asym["A"] < sym["A"]  # ...at A's expense
    assert abs(sum(asym.values()) - sum(w.values())) < 1e-9  # deployment preserved
    assert abs(sum(sym.values()) - sum(w.values())) < 1e-9


def test_tilt_no_data_is_noop():
    w = {"BNB": 0.3, "ETH": 0.2}
    assert momentum_tilt(w, {}) == w
    assert momentum_tilt(w, {"BNB": {"pct_7d": None}, "ETH": {"pct_7d": None}}) == w


def test_tilt_missing_token_keeps_weight_and_total():
    w = {"BNB": 0.4, "ETH": 0.4, "CAKE": 0.2}
    tc = {"BNB": {"pct_7d": 10.0}, "ETH": {"pct_7d": -10.0}}  # CAKE has no CMC data
    t = momentum_tilt(w, tc)
    assert t["CAKE"] > 0
    assert abs(sum(t.values()) - sum(w.values())) < 1e-9


# --------------------------------------------------------------------------- #
# cmc_momentum_tilt — CMC-native multi-window momentum (pct_24h/7d/30d) sizing tilt
# --------------------------------------------------------------------------- #
def test_cmc_momentum_tilt_noop_at_zero_weight():
    w = {"BNB": 0.3, "ETH": 0.2}
    sigs = {"BNB": {"pct_24h": 5.0, "pct_7d": 8.0}, "ETH": {"pct_24h": -3.0, "pct_7d": -1.0}}
    assert cmc_momentum_tilt(w, sigs, w=0.0) == w


def test_cmc_momentum_tilt_preserves_total_deployment():
    w = {"BNB": 0.3, "ETH": 0.2, "CAKE": 0.1}
    sigs = {
        "BNB": {"pct_24h": 5.0, "pct_7d": 8.0, "pct_30d": 12.0},
        "ETH": {"pct_24h": -3.0, "pct_7d": -1.0, "pct_30d": 2.0},
        "CAKE": {"pct_24h": 1.0, "pct_7d": 0.0, "pct_30d": -4.0},
    }
    t = cmc_momentum_tilt(w, sigs, w=0.12)
    assert abs(sum(t.values()) - sum(w.values())) < 1e-9


def test_cmc_momentum_tilt_favors_stronger_momentum():
    w = {"BNB": 0.25, "ETH": 0.25}
    sigs = {"BNB": {"pct_24h": 10.0, "pct_7d": 10.0}, "ETH": {"pct_24h": -10.0, "pct_7d": -10.0}}
    t = cmc_momentum_tilt(w, sigs, w=0.12)
    assert t["BNB"] > t["ETH"]


def test_cmc_momentum_tilt_is_bounded():
    w = {"A": 0.5, "B": 0.5}
    sigs = {"A": {"pct_24h": 1e4, "pct_7d": 1e4}, "B": {"pct_24h": -1e4, "pct_7d": -1e4}}
    t = cmc_momentum_tilt(w, sigs, w=10.0)  # huge w → clamp must bind
    assert max(t.values()) / min(t.values()) <= (1.15 / 0.85) + 1e-9


def test_cmc_momentum_tilt_sparse_is_noop():
    # <2 tokens with a momentum reading → no partial tilt.
    w = {"BNB": 0.3, "ETH": 0.2}
    assert cmc_momentum_tilt(w, {}, w=0.12) == w
    assert cmc_momentum_tilt(w, {"BNB": {"pct_24h": 5.0}}, w=0.12) == w


def test_cmc_momentum_tilt_missing_token_keeps_weight_and_total():
    w = {"BNB": 0.4, "ETH": 0.4, "CAKE": 0.2}
    sigs = {"BNB": {"pct_24h": 10.0}, "ETH": {"pct_24h": -10.0}}  # CAKE has no reading
    t = cmc_momentum_tilt(w, sigs, w=0.12)
    assert t["CAKE"] > 0
    assert abs(sum(t.values()) - sum(w.values())) < 1e-9


# --------------------------------------------------------------------------- #
# sector_tilt — rotate the held book toward CMC's live trending narratives
# --------------------------------------------------------------------------- #
_SECTORS = {"BNB": {"Layer 1", "Binance Ecosystem"}, "ETH": {"Layer 1", "Smart Contracts"},
            "DOGE": {"Memes"}, "CAKE": {"DeFi"}}


def test_sector_tilt_noop_at_zero_weight_or_empty_trending():
    w = {"DOGE": 0.5, "ETH": 0.5}
    assert sector_tilt(w, ["Memes"], _SECTORS, w=0.0) == w
    assert sector_tilt(w, [], _SECTORS, w=0.10) == w
    assert sector_tilt(w, None, _SECTORS, w=0.10) == w


def test_sector_tilt_boosts_trending_member_preserving_total():
    w = {"DOGE": 0.5, "ETH": 0.5}
    t = sector_tilt(w, ["Memes"], _SECTORS, w=0.10)  # DOGE in trending Memes, ETH not
    assert t["DOGE"] > w["DOGE"]
    assert t["ETH"] < w["ETH"]
    assert abs(sum(t.values()) - sum(w.values())) < 1e-9


def test_sector_tilt_noop_when_no_held_token_matches():
    w = {"ETH": 0.5, "CAKE": 0.5}
    assert sector_tilt(w, ["Memes"], _SECTORS, w=0.10) == w  # neither is a Meme


def test_sector_tilt_all_trending_is_unchanged():
    # When every held token matches, equal multipliers + renorm → no differential, no tilt.
    w = {"BNB": 0.5, "ETH": 0.5}  # both Layer 1
    t = sector_tilt(w, ["Layer 1"], _SECTORS, w=0.10)
    assert t == w


def test_sector_tilt_case_insensitive_and_bounded():
    w = {"DOGE": 0.5, "ETH": 0.5}
    t = sector_tilt(w, ["memes"], _SECTORS, w=0.10)  # lowercase trending still matches "Memes"
    assert t["DOGE"] > t["ETH"]
    # boost is min(hi, 1+w); ratio of pre-renorm multipliers is bounded by hi/1.
    assert (t["DOGE"] / w["DOGE"]) / (t["ETH"] / w["ETH"]) <= (1.15 / 1.0) + 1e-9


def test_sector_tilt_unknown_token_never_raises():
    w = {"ZZZ": 0.5, "DOGE": 0.5}  # ZZZ not in the sector map
    t = sector_tilt(w, ["Memes"], _SECTORS, w=0.10)
    assert abs(sum(t.values()) - sum(w.values())) < 1e-9
    assert t["DOGE"] > t["ZZZ"]


# --------------------------------------------------------------------------- #
# weight_path_tilted — the tilt in the backtest engine
# --------------------------------------------------------------------------- #
def test_weight_path_tilted_preserves_deployment_and_shifts_split():
    c = _trend_close()
    p = AllocatorParams()
    wp = weight_path(c, p)
    wpt = weight_path_tilted(c, p)
    assert np.allclose(wp.sum(axis=1), wpt.sum(axis=1), atol=1e-9)  # total deployment unchanged
    assert not np.allclose(wp, wpt)  # but the within-row split shifts


def test_weight_path_tilted_preserves_cap_series_deployment():
    c = _trend_close()
    p = AllocatorParams()
    cap = np.full(c.shape[0], 0.5)
    wp = weight_path(c, p, cap_series=cap)
    wpt = weight_path_tilted(c, p, cap_series=cap)
    assert np.allclose(wp.sum(axis=1), wpt.sum(axis=1), atol=1e-9)


# --------------------------------------------------------------------------- #
# weight_path_ranked — the CMC multi-timeframe blended ranking ("go deeper")
# --------------------------------------------------------------------------- #
def _geom_close(n: int = 400, k: int = 8):
    """Distinct geometric trends → distinct, stable 120-bar ranking (no ties)."""
    rates = 1.0 + (np.arange(k) + 1) * 0.001
    t = np.arange(n)[:, None].astype(float)
    return 100.0 * rates[None, :] ** t


def _crossing_close(n: int = 600, k: int = 8):
    """Long-term trend + a per-token recent oscillation → short vs long ranking disagree."""
    t = np.arange(n).astype(float)
    out = np.zeros((n, k))
    for j in range(k):
        out[:, j] = 100.0 + t * (0.05 * (j + 1)) + 6.0 * np.sin(t / 25.0 + j)
    return out


def test_weight_path_ranked_reduces_to_baseline_at_trivial_blend():
    c = _geom_close()
    p = AllocatorParams()
    assert np.allclose(weight_path(c, p), weight_path_ranked(c, p))  # default blend={lookback:1}
    cap = np.full(c.shape[0], 0.5)
    assert np.allclose(weight_path(c, p, cap_series=cap), weight_path_ranked(c, p, cap_series=cap))


def test_weight_path_ranked_short_blend_changes_picks():
    c = _crossing_close()
    p = AllocatorParams()
    base = weight_path(c, p)
    short = weight_path_ranked(c, p, blend={42: 1.0})  # pure 7d ranking
    assert not np.allclose(base, short)


def test_weight_path_ranked_ta_inert_at_zero_weight():
    # CMC TA confirmation must be a no-op at w_ta_rank=0 (regression: ranking unchanged).
    c = _geom_close()
    p = AllocatorParams()
    ta = np.random.default_rng(0).random(c.shape)  # arbitrary per-bar TA
    assert np.allclose(
        weight_path_ranked(c, p), weight_path_ranked(c, p, ta_score=ta, w_ta_rank=0.0)
    )


def test_weight_path_ranked_ta_confirmation_changes_picks():
    # A strong TA tilt (confirm a low-momentum name, penalise the top one) reorders the top-k.
    c = _geom_close()
    p = AllocatorParams()
    n, k = c.shape
    base = weight_path_ranked(c, p)
    ta = np.full((n, k), 0.5)
    ta[:, 0] = 1.0  # confirm token 0 (low momentum here)
    ta[:, -1] = 0.0  # penalise the top-momentum token
    with_ta = weight_path_ranked(c, p, ta_score=ta, w_ta_rank=3.0)
    assert not np.allclose(base, with_ta)
