"""
Enhanced-regime (CMC Startup tier) tests.

The CRITICAL guarantee: with no intel (the offline/contest path) the score is
bit-for-bit the validated model. The new LIVE-only terms (BTC-dominance trend,
total-mktcap trend, F&G momentum) only move the score when supplied + weighted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.agent.rationale import explain
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS
from ictbot.strategy.regime_score import (
    RegimeIntel,
    _dominance_term,
    _fng_mom_term,
    _mktcap_term,
    regime_breakdown,
    regime_score,
)


def _close(n: int = 60, k: int = 4) -> np.ndarray:
    """A gently up-trending k-token close matrix (deterministic)."""
    col = np.arange(n)[:, None] * 0.5 + 100.0
    return np.tile(col, (1, k)) + np.arange(k)[None, :] * 0.1


# --------------------------------------------------------------------------- #
# Backward compatibility (the regression that protects the contest entry)
# --------------------------------------------------------------------------- #
def test_intel_none_equals_empty_intel():
    c = _close()
    i = c.shape[0] - 1
    # Empty intel adds no terms → identical to the validated intel-free score.
    assert regime_score(c, i, intel=None) == regime_score(c, i, intel=RegimeIntel())


def test_weight_zero_disables_term():
    c = _close()
    i = c.shape[0] - 1
    base = regime_score(c, i, intel=None)
    s = regime_score(
        c, i, intel=RegimeIntel(btc_dominance=55, btc_dominance_prev=60, w_dominance=0.0)
    )
    assert s == base


# --------------------------------------------------------------------------- #
# Each new term moves the score in the right direction
# --------------------------------------------------------------------------- #
def test_falling_dominance_raises_score():
    c, i = _close(), _close().shape[0] - 1
    fall = regime_score(c, i, intel=RegimeIntel(btc_dominance=55, btc_dominance_prev=60))
    rise = regime_score(c, i, intel=RegimeIntel(btc_dominance=60, btc_dominance_prev=55))
    assert fall > rise


def test_expanding_mktcap_raises_score():
    c, i = _close(), _close().shape[0] - 1
    exp = regime_score(c, i, intel=RegimeIntel(total_mktcap=2.2e12, total_mktcap_prev=2.0e12))
    con = regime_score(c, i, intel=RegimeIntel(total_mktcap=1.8e12, total_mktcap_prev=2.0e12))
    assert exp > con


def test_fng_momentum_raises_score():
    c, i = _close(), _close().shape[0] - 1
    up = regime_score(c, i, intel=RegimeIntel(fng_now=30, fng_7d_avg=20))
    dn = regime_score(c, i, intel=RegimeIntel(fng_now=20, fng_7d_avg=30))
    assert up > dn


def test_term_math_and_none_guards():
    assert _dominance_term(RegimeIntel(btc_dominance=55, btc_dominance_prev=60)) > 0.5
    assert _dominance_term(RegimeIntel(btc_dominance=60, btc_dominance_prev=55)) < 0.5
    assert _dominance_term(RegimeIntel()) is None  # missing inputs
    assert _mktcap_term(RegimeIntel(total_mktcap=2.2e12, total_mktcap_prev=2.0e12)) > 0.5
    assert _fng_mom_term(RegimeIntel(fng_now=40, fng_7d_avg=20)) > 0.5
    assert _fng_mom_term(RegimeIntel(fng_now=None)) is None


# --------------------------------------------------------------------------- #
# Breakdown (journal + dashboard)
# --------------------------------------------------------------------------- #
def test_regime_breakdown_has_all_terms():
    df = pd.DataFrame(_close(n=60, k=len(CONTEST_TOKENS)), columns=list(CONTEST_TOKENS))
    bd = regime_breakdown(
        df,
        fear_greed=50,
        intel=RegimeIntel(
            btc_dominance=55,
            btc_dominance_prev=60,
            total_mktcap=2.2e12,
            total_mktcap_prev=2.0e12,
            fng_now=30,
            fng_7d_avg=20,
        ),
    )
    assert {
        "breadth",
        "trend",
        "vol_factor",
        "fng",
        "dominance",
        "mktcap",
        "fng_mom",
        "score",
    } <= set(bd)
    assert 0.0 <= bd["score"] <= 1.0


def test_regime_breakdown_offline_omits_intel_terms():
    df = pd.DataFrame(_close(n=60, k=len(CONTEST_TOKENS)), columns=list(CONTEST_TOKENS))
    bd = regime_breakdown(df, fear_greed=None, intel=None)
    assert "dominance" not in bd and bd["fng"] is None


# --------------------------------------------------------------------------- #
# Rationale macro clause (faithful, no LLM)
# --------------------------------------------------------------------------- #
def test_rationale_macro_clause():
    s = explain(
        fear_greed=14,
        regime_score=0.4,
        deploy_cap=0.5,
        weights={"BNB": 0.3},
        intel={
            "btc_dominance": 58,
            "btc_dominance_prev": 60,
            "total_mktcap": 2.1e12,
            "total_mktcap_prev": 2.7e12,
            "fng_now": 14,
            "fng_7d_avg": 17,
        },
    )
    assert "CMC macro" in s and "falling" in s and "contracting" in s and "cooling" in s


def test_rationale_without_intel_is_unchanged():
    s = explain(fear_greed=14, regime_score=0.4, deploy_cap=0.5, weights={"BNB": 0.3})
    assert "CMC macro" not in s
