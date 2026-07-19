"""
J16 (audit gap #25) — property tests on ICTProMaxStrategy.evaluate.

The strategy is where the bugs hide. Per-direction blockers, fade flip
geometry, ATR vs fraction stops, gate short-circuits — too many
combinations for hand-rolled cases. Property tests cover the invariants
the strategy must always satisfy regardless of input.
"""

from __future__ import annotations

import math

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _ohlcv(n: int, base: float, slope: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=n, freq="1min"),
            "open": [base + i * slope for i in range(n)],
            "high": [base + i * slope + 1 for i in range(n)],
            "low": [base + i * slope - 1 for i in range(n)],
            "close": [base + i * slope + 0.3 for i in range(n)],
            "volume": [10.0] * n,
        }
    )


def _session():
    return {
        "india_time": "00:00:00",
        "tokyo_time": "00:00:00",
        "tokyo_status": "CLOSED",
        "london_time": "00:00:00",
        "london_status": "CLOSED",
        "newyork_time": "00:00:00",
        "newyork_status": "CLOSED",
        "active_session": "OFF",
        "killzone_active": False,
        "allow_trade": True,
    }


# ---- shape invariants ------------------------------------------------------


@given(base=st.floats(min_value=0.0001, max_value=100_000.0))
@settings(max_examples=20, deadline=None)
def test_evaluate_always_returns_required_keys(base):
    """Every result dict must carry the keys downstream code reads."""
    strat = ICTProMaxStrategy()
    r = strat.evaluate(
        _ohlcv(60, base),
        _ohlcv(40, base),
        _ohlcv(40, base),
        _ohlcv(30, base),
        _session(),
        pair="TEST",
    )
    required = {
        "pair",
        "error",
        "price",
        "last_close",
        "htf_bias",
        "ltf_bias",
        "ltf_poi",
        "poi_tap",
        "ltf_mss",
        "fvg",
        "micro_fvg",
        "delta",
        "atr_1m",
        "entry",
        "sl",
        "tp",
        "rr",
        "confidence",
        "gate_blocked",
        "regime",
        "diagnostics",
    }
    assert required <= set(r.keys())


@given(base=st.floats(min_value=0.01, max_value=10_000.0))
@settings(max_examples=20, deadline=None)
def test_confidence_is_one_of_five_buckets(base):
    """Four weighted bits → 0/25/50/75/100. Never anything else."""
    strat = ICTProMaxStrategy()
    r = strat.evaluate(
        _ohlcv(60, base),
        _ohlcv(40, base),
        _ohlcv(40, base),
        _ohlcv(30, base),
        _session(),
        pair="X",
    )
    assert r["confidence"] in (0, 25, 50, 75, 100)


# ---- entry geometry --------------------------------------------------------


@given(
    base=st.floats(min_value=10.0, max_value=10_000.0),
    sl_frac=st.floats(min_value=0.001, max_value=0.05),
    tp_frac=st.floats(min_value=0.001, max_value=0.10),
)
@settings(max_examples=50, deadline=None)
def test_buy_geometry_when_fired(base, sl_frac, tp_frac):
    """For any BUY that actually fires, the SL must be below entry and
    TP above entry (or zero, meaning no signal)."""
    strat = ICTProMaxStrategy(
        strategy_mode="follow",
        sl_frac=sl_frac,
        tp_frac=tp_frac,
    )
    r = strat.evaluate(
        _ohlcv(60, base, slope=base * 0.001),  # gentle uptrend
        _ohlcv(40, base),
        _ohlcv(40, base),
        _ohlcv(30, base),
        _session(),
        pair="X",
    )
    if r["entry"] == "BUY":
        assert r["sl"] < r["price"] < r["tp"], (
            f"BUY geometry broken: sl={r['sl']} price={r['price']} tp={r['tp']}"
        )
    elif r["entry"] == "SELL":
        assert r["tp"] < r["price"] < r["sl"]


# ---- fade flip is an involution -------------------------------------------


def test_fade_then_follow_recovers_same_direction():
    """Running the SAME data twice — once in fade mode, once in follow
    mode — must produce OPPOSITE entry directions when both fire."""
    df_h, df_b, df_p, df_e = (
        _ohlcv(60, 100.0, 0.05),
        _ohlcv(40, 100.0),
        _ohlcv(40, 100.0),
        _ohlcv(30, 100.0),
    )
    s_follow = ICTProMaxStrategy(strategy_mode="follow", delta_window=20)
    s_fade = ICTProMaxStrategy(strategy_mode="fade", delta_window=20)

    r_follow = s_follow.evaluate(df_h, df_b, df_p, df_e, _session(), "X")
    r_fade = s_fade.evaluate(df_h, df_b, df_p, df_e, _session(), "X")

    # If both fired, directions must be opposite.
    if r_follow["entry"] in ("BUY", "SELL") and r_fade["entry"] in ("BUY", "SELL"):
        assert {r_follow["entry"], r_fade["entry"]} == {"BUY", "SELL"}


# ---- killzone short-circuits ------------------------------------------------


def test_killzone_required_with_inactive_zone_forces_no_entry():
    """When killzone_required=True and session.killzone_active=False, no
    signal can fire regardless of the indicator stack."""
    session = _session()
    assert session["killzone_active"] is False
    strat = ICTProMaxStrategy(strategy_mode="follow", killzone_required=True)
    r = strat.evaluate(
        _ohlcv(60, 100.0, slope=0.5),
        _ohlcv(40, 100.0),
        _ohlcv(40, 100.0),
        _ohlcv(30, 100.0),
        session,
        pair="X",
    )
    assert r["entry"] == "NO ENTRY"
    assert r["gate_blocked"] is not None
    assert "killzone" in r["gate_blocked"].lower()


# ---- diagnostic alignment with delta_mode (J14) ---------------------------


def test_diag_under_relative_delta_mentions_relative():
    """J14: when delta_mode='relative' fires, blockers must reference
    rel_delta, not raw delta. Otherwise debugging output lies."""
    strat = ICTProMaxStrategy(delta_mode="relative", relative_delta_threshold=0.5)
    r = strat.evaluate(
        _ohlcv(60, 100.0, slope=-0.5),  # downtrend so HTF=BEARISH
        _ohlcv(40, 100.0),
        _ohlcv(40, 100.0),
        _ohlcv(30, 100.0),
        _session(),
        pair="X",
    )
    # Inspect both blocker lists for a "Relative delta is …" string.
    all_blockers = r["diagnostics"]["buy_blockers"] + r["diagnostics"]["sell_blockers"]
    has_relative = any("Relative delta" in b for b in all_blockers)
    has_raw = any(b.startswith("Delta is") for b in all_blockers)
    # Relative mode must produce relative-delta diagnostics; raw delta
    # blocker is reserved for the legacy "sign" mode.
    assert has_relative or not has_raw  # either says relative or says neither


# ---- error path stays inside the dict -------------------------------------


def test_too_short_dataframes_produce_error_field_not_exception():
    """If any frame is too short, evaluate must return a result with
    `error` populated — not raise."""
    strat = ICTProMaxStrategy()
    r = strat.evaluate(
        _ohlcv(5, 100.0),  # too short for HTF (MIN_BARS=50)
        _ohlcv(40, 100.0),
        _ohlcv(40, 100.0),
        _ohlcv(30, 100.0),
        _session(),
        pair="X",
    )
    assert r["error"] is not None
    assert r["entry"] == "NO ENTRY"


# ---- NaN/inf-safety -------------------------------------------------------


def test_finite_outputs_on_well_formed_input():
    """price/sl/tp/rr must all be finite numbers when no error."""
    strat = ICTProMaxStrategy()
    r = strat.evaluate(
        _ohlcv(60, 100.0),
        _ohlcv(40, 100.0),
        _ohlcv(40, 100.0),
        _ohlcv(30, 100.0),
        _session(),
        pair="X",
    )
    if r["error"]:
        return
    for k in ("price", "sl", "tp", "rr"):
        assert math.isfinite(r[k]), f"{k} not finite: {r[k]}"
