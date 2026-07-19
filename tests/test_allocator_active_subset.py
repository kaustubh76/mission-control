"""UI token toggles — adaptive_target_weights(active=...) restricts the RANKING
universe while regime/breadth stays full-universe. active=None must be bit-for-bit
the legacy path (same A/B-gate discipline as every other lever)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams
from ictbot.strategy.regime_score import adaptive_target_weights

N = 280


def matrix(series: dict[str, np.ndarray]) -> pd.DataFrame:
    df = pd.DataFrame(series)
    df.insert(0, "time", pd.date_range("2024-01-01", periods=len(df), freq="4h"))
    return df.set_index("time")


def staggered_bull() -> pd.DataFrame:
    """Every token trends up; later tokens trend HARDER (momentum order = column order)."""
    return matrix({t: 100.0 + (0.2 + 0.1 * i) * np.arange(N) for i, t in enumerate(CONTEST_TOKENS)})


def test_active_none_is_bit_for_bit_legacy():
    p = AllocatorParams(top_k=2)
    df = staggered_bull()
    base, s_base, c_base = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85)
    none_, s_n, c_n = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85, active=None)
    assert base == none_ and s_base == s_n and c_base == c_n


def test_active_full_universe_is_bit_for_bit_legacy():
    p = AllocatorParams(top_k=2)
    df = staggered_bull()
    base, *_ = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85)
    full, *_ = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85, active=list(CONTEST_TOKENS))
    assert base == full


def test_disabled_strongest_token_is_excluded():
    """The top-momentum token (last column) is deselected -> top-k comes from the
    remaining active set; the deselected token's weight is exactly 0.0."""
    p = AllocatorParams(top_k=2)
    df = staggered_bull()
    strongest = CONTEST_TOKENS[-1]
    base, *_ = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85)
    assert base[strongest] > 0  # sanity: held at baseline
    active = [t for t in CONTEST_TOKENS if t != strongest]
    w, score, cap = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85, active=active)
    assert w[strongest] == 0.0
    held = {k for k, v in w.items() if v > 0}
    assert held <= set(active) and len(held) == p.top_k
    # regime score is a market gauge — unchanged by the portfolio restriction
    _, base_score, base_cap = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85)
    assert score == base_score and cap == base_cap


def test_weights_keys_cover_full_universe_with_zeros():
    p = AllocatorParams(top_k=2)
    df = staggered_bull()
    w, *_ = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85, active=["BNB", "ETH", "CAKE"])
    assert set(w.keys()) == set(CONTEST_TOKENS)  # journal/dashboard shape unchanged
    assert {k for k, v in w.items() if v > 0} <= {"BNB", "ETH", "CAKE"}


def test_two_active_edge_still_deploys():
    p = AllocatorParams(top_k=2)
    df = staggered_bull()
    w, _, cap = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85, active=["BNB", "ETH"])
    held = {k for k, v in w.items() if v > 0}
    assert held == {"BNB", "ETH"}
    assert abs(sum(w.values()) - cap) < 1e-9  # fully deploys to the cap


def test_empty_active_degrades_to_full_universe():
    """Defence in depth: an empty list never silently zeroes the book."""
    p = AllocatorParams(top_k=2)
    df = staggered_bull()
    base, *_ = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85)
    empty, *_ = adaptive_target_weights(df, p, floor=0.40, ceiling=0.85, active=[])
    assert base == empty


def test_ta_rank_composes_with_active_subset():
    """ta_rank tilt + active subset: ranking still confined to the active set."""
    p = AllocatorParams(top_k=2)
    df = staggered_bull()
    active = ["BNB", "ETH", "CAKE", "LINK"]
    tilt = {t: 0.5 for t in CONTEST_TOKENS}
    tilt["BNB"] = 1.0  # strongly confirm the weakest active
    w, *_ = adaptive_target_weights(
        df, p, floor=0.40, ceiling=0.85, active=active, ta_token_scores=tilt, w_ta_rank=10.0
    )
    held = {k for k, v in w.items() if v > 0}
    assert held <= set(active)
