"""
End-to-end integration test for the full canonical ICT flow
(Phases A→F combined).

Goal: one path through `ICTProMaxStrategy.evaluate` with EVERY canonical
knob on, asserting the result dict matches what the spec promises:

  - entry fires as BUY (we feed bullish data)
  - sl is anchored to the MFVG floor (Box 7)
  - tp is exactly 1:2 RR off the real R distance (Box 8a)
  - tp2 is the next unbroken liquidity level above price (Box 8b)
  - diagnostics report 5/5 canonical gates passed
  - confidence is 100

We can't reasonably synthesise frames that satisfy every gate naturally
(too many interacting conditions over too many bars), so we monkey-patch
the upstream gate functions to known-good values, then exercise the
*orchestration* — the strategy's job of stitching the gates together
and computing the bracket. The indicator-level tests already cover
each gate's internal correctness.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _bar(o, h, l, c, v=1.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _flat(n: int, close: float = 100.0) -> pd.DataFrame:
    """Padding frame that satisfies MIN_BARS without triggering any
    indicator gate accidentally."""
    return pd.DataFrame([_bar(close, close + 1, close - 1, close) for _ in range(n)])


def _session():
    return {
        "killzone_active": True,
        "india_time": "10:00",
        "tokyo_time": "13:30",
        "tokyo_status": "OPEN",
        "london_time": "05:30",
        "london_status": "OPEN",
        "newyork_time": "00:30",
        "newyork_status": "CLOSED",
        "active_session": "LONDON",
    }


def _all_canonical_gates_pass(monkeypatch):
    """Patch every gate the canonical flow checks to return PASS for a
    BUY setup. Returns the captured-arg dict so tests can assert what
    the strategy fed each gate."""
    from ictbot.strategy import ict_pro_max as strat_mod

    seen: dict = {}

    # Box 1: bias = BULLISH on both HTF and LTF (LTF diagnostic only).
    monkeypatch.setattr(
        strat_mod.ICTProMaxStrategy,
        "_get_htf_bias",
        lambda self, df: "BULLISH",
    )
    monkeypatch.setattr(
        strat_mod.ICTProMaxStrategy,
        "_get_ltf_bias",
        lambda self, df: "BULLISH",
    )

    # Box 2: POI tapped (htf_then_poi tries htf, that's enough).
    monkeypatch.setattr(
        strat_mod, "get_ob_poi", lambda df, bias, mitigation_bars=None, tick_size=None: 100.0
    )
    monkeypatch.setattr(strat_mod, "get_ltf_poi", lambda df, bias, tick_size=None: 100.0)
    monkeypatch.setattr(strat_mod, "get_poi_tap", lambda df, poi, tolerance_frac=None: "POI TAPPED")
    monkeypatch.setattr(
        strat_mod, "is_mitigated", lambda df, poi, side="demand", retire_bars=None: False
    )

    # Box 3: MSS confirmed on the POI frame.
    monkeypatch.setattr(strat_mod, "get_ltf_mss", lambda df, bias, mode="swing": "BULLISH MSS")
    monkeypatch.setattr(
        strat_mod,
        "get_ltf_mss_time",
        lambda df, bias, mode="swing": pd.Timestamp("2026-01-01 12:00"),
    )

    # Box 4: MFVG exists, formed after MSS. We control its range.
    def _fvg_info(df, bias, mitigation_bars=None, *, min_formation_time=None):
        seen["fvg_min_time"] = min_formation_time
        return {
            "low": 95.0,  # gap floor → SL anchors here
            "high": 99.0,
            "formation_index": -1,
            "formation_time": pd.Timestamp("2026-01-01 13:00"),
        }

    monkeypatch.setattr(strat_mod, "get_micro_fvg_info", _fvg_info)
    # Same returned range when the structural anchor re-queries.
    monkeypatch.setattr(
        strat_mod,
        "get_micro_fvg_range",
        lambda df, bias, mitigation_bars=None, *, min_formation_time=None: (95.0, 99.0),
    )

    # Box 5: MFVG retested.
    monkeypatch.setattr(
        strat_mod, "has_mfvg_retest", lambda df, fvg_low, fvg_high, formation_time: True
    )

    # Delta gate: bias-aligned buys.
    monkeypatch.setattr(strat_mod, "get_delta", lambda df: 5.0)
    monkeypatch.setattr(strat_mod, "get_relative_delta", lambda df: 1.0)
    monkeypatch.setattr(strat_mod, "get_atr", lambda df, period=14: 0.5)

    # Box 8b: next liquidity above price = 130.
    monkeypatch.setattr(
        strat_mod,
        "get_next_liquidity_level",
        lambda df, direction, price, **kw: 130.0 if direction == "BUY" else None,
    )

    return seen


def test_canonical_flow_e2e_buy_fires_with_structural_bracket(monkeypatch):
    """Full BUY setup. Every gate set to PASS via monkey-patches. Assert
    the result dict matches what the spec promises."""
    seen = _all_canonical_gates_pass(monkeypatch)

    # current_price = entry_df["close"].iloc[-1]. We set the entry frame's
    # last close to 100 so R = 100 - 95 = 5, TP = 100 + 2*5 = 110.
    htf = _flat(60, close=100)
    bias = _flat(30, close=100)
    poi = _flat(40, close=100)
    entry_df = _flat(10, close=100)

    s = ICTProMaxStrategy(
        # Phase A canonical defaults are already on; spelling them out so
        # the test reads as documentation of the canonical configuration.
        bias_engine="swing",
        poi_engine="order_block",
        strategy_mode="follow",
        # Phase B–F
        mss_timeframe="poi",
        require_fvg_after_mss=True,
        require_mfvg_retest=True,
        poi_frame="htf_then_poi",
        # Phase 7/8
        sl_anchor="structural",
        structural_tp1_rr=2.0,
        # Required for the FVG / retest gate to be considered satisfied.
        require_fvg=True,
    )
    out = s.evaluate(htf, bias, poi, entry_df, _session())

    # Box 6: entry fires as BUY (follow + bullish bias + everything passes).
    assert out["entry"] == "BUY"

    # Box 7: SL = MFVG floor (95.0, tick-rounded — tick_size is None so
    # rounded to 2dp by the legacy round_to_tick fallback).
    assert out["sl"] == pytest.approx(95.0, abs=0.5)

    # Box 8a: TP1 = entry + 2R = 100 + 2*(100-95) = 110.
    assert out["tp"] == pytest.approx(110.0, abs=0.5)

    # Box 8b: TP2 = liquidity target the monkey-patched finder returns.
    assert out["tp2"] == pytest.approx(130.0, abs=0.5)

    # 4 confidence bits all set → 100.
    assert out["confidence"] == 100

    # Sanity: MSS time was passed into the FVG search as min_formation_time.
    # Confirms Phase C is wired through.
    assert seen.get("fvg_min_time") == pd.Timestamp("2026-01-01 12:00")

    # Diagnostics: no blockers on the BUY side (all canonical gates passed).
    assert out["diagnostics"]["buy_blockers"] == []
    assert out["diagnostics"]["closest_direction"] == "BUY"


def test_canonical_flow_e2e_one_gate_failing_blocks_entry(monkeypatch):
    """Negative case: turn the retest gate off (= NOT retested) and
    assert the strategy refuses to fire, surfacing the missing piece
    in diagnostics."""
    _all_canonical_gates_pass(monkeypatch)
    # Override only the retest result — every other gate still PASS.
    from ictbot.strategy import ict_pro_max as strat_mod

    monkeypatch.setattr(
        strat_mod, "has_mfvg_retest", lambda df, fvg_low, fvg_high, formation_time: False
    )

    s = ICTProMaxStrategy(
        bias_engine="swing",
        poi_engine="order_block",
        strategy_mode="follow",
        mss_timeframe="poi",
        require_fvg_after_mss=True,
        require_mfvg_retest=True,
        poi_frame="htf_then_poi",
        sl_anchor="structural",
        structural_tp1_rr=2.0,
        require_fvg=True,
    )
    out = s.evaluate(_flat(60), _flat(30), _flat(40), _flat(10), _session())

    assert out["entry"] == "NO ENTRY"
    blockers = out["diagnostics"]["blockers"]
    assert any("MFVG not retested" in b for b in blockers), (
        f"expected the missing retest in {blockers}"
    )


def test_canonical_flow_kill_switch_makes_everything_revert(monkeypatch):
    """`CANONICAL_FLOW=off` must roll every default back. This test
    asserts the rollback is comprehensive — if a new phase adds a knob
    without wiring the kill-switch, this catches it."""
    import importlib
    import os

    saved = os.environ.get("CANONICAL_FLOW")
    try:
        os.environ["CANONICAL_FLOW"] = "off"
        import ictbot.settings as smod

        importlib.reload(smod)

        # Every canonical-flow-controlled setting must be at its
        # PRE-canonical legacy value when the kill-switch is engaged.
        assert smod.settings.strategy_mode == "fade"
        assert smod.settings.bias_engine == "sma"
        assert smod.settings.poi_engine == "min_max"
        assert smod.settings.sl_anchor == "fixed"
        assert smod.settings.mss_timeframe == "entry"
        assert smod.settings.require_fvg_after_mss is False
        assert smod.settings.require_mfvg_retest is False
        assert smod.settings.poi_frame == "poi"
    finally:
        if saved is None:
            os.environ.pop("CANONICAL_FLOW", None)
        else:
            os.environ["CANONICAL_FLOW"] = saved
        import ictbot.settings as smod

        importlib.reload(smod)
