"""
Phase E — HTF/LTF bias alignment gate tests.

The gate refuses to fire when 4h HTF bias and 15m LTF bias disagree.
Stops the "short into bullish LTF momentum" pattern that produced
21/21 closed SELLs at 5.6% win rate in the first live run.

Strategy default is OFF (so synthetic test fixtures elsewhere don't
need to align both frames). Settings default is ON (so production
runs are guarded). These tests exercise both.
"""

from __future__ import annotations

import pandas as pd

from ictbot.strategy.ict_pro_max import ICTProMaxStrategy, _diagnose

# ---- helpers --------------------------------------------------------------


def _flat(n: int) -> pd.DataFrame:
    """Minimum OHLC frame to reach evaluate body without tripping data checks."""
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1.0,
            }
            for i in range(n)
        ]
    )


def _session() -> dict:
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


def _patch_all_gates_pass(monkeypatch, *, htf_bias: str, ltf_bias: str):
    """Stub every gate upstream of the bias-alignment check so the only
    failure mode left is the alignment itself. Returns a callable that
    accepts a custom strategy and runs evaluate.
    """
    from ictbot.strategy import ict_pro_max as strat_mod

    mss_label = "BULLISH MSS" if htf_bias == "BULLISH" else "BEARISH MSS"

    monkeypatch.setattr(strat_mod, "get_swing_bias", lambda df: htf_bias)
    monkeypatch.setattr(strat_mod, "sma_htf_bias", lambda df: htf_bias)
    monkeypatch.setattr(strat_mod, "sma_ltf_bias", lambda df: ltf_bias)
    # Strategy uses get_swing_bias for BOTH frames under bias_engine="swing".
    # We need the LTF call to return ltf_bias too. Patch the method directly
    # rather than the module function so HTF and LTF can differ.

    monkeypatch.setattr(strat_mod, "get_ob_poi", lambda df, bias, **kw: 100.0)
    monkeypatch.setattr(
        strat_mod, "get_poi_tap", lambda df, level, tolerance_frac=0.0: "POI TAPPED"
    )
    monkeypatch.setattr(strat_mod, "is_mitigated", lambda df, level, side, retire_bars: False)
    monkeypatch.setattr(strat_mod, "get_ltf_mss", lambda df, bias, mode="swing": mss_label)
    monkeypatch.setattr(
        strat_mod,
        "get_ltf_mss_time",
        lambda df, bias, mode="swing": pd.Timestamp("2026-01-01 11:00"),
    )
    monkeypatch.setattr(
        strat_mod,
        "get_micro_fvg_info",
        lambda df, bias, mitigation_bars=None, *, min_formation_time=None: {
            "low": 99,
            "high": 101,
            "formation_index": -1,
            "formation_time": pd.Timestamp("2026-01-01 12:00"),
        },
    )
    monkeypatch.setattr(
        strat_mod, "has_mfvg_retest", lambda df, fvg_low, fvg_high, formation_time: True
    )
    # Delta: positive for BULLISH (so delta_buy), negative for BEARISH (delta_sell).
    monkeypatch.setattr(strat_mod, "get_delta", lambda df: 1.0 if htf_bias == "BULLISH" else -1.0)
    monkeypatch.setattr(strat_mod, "get_relative_delta", lambda df: 0.0)
    monkeypatch.setattr(strat_mod, "get_atr", lambda df, period=14: 0.5)
    monkeypatch.setattr(strat_mod, "atr_percentile_regime", lambda df: "HIGH_VOL")


def _run_strategy(strategy: ICTProMaxStrategy, htf_bias: str, ltf_bias: str, monkeypatch) -> dict:
    """Drive the strategy with the bias outcomes we want by patching the
    instance methods rather than the module functions (so HTF and LTF
    can differ even though both go through `get_swing_bias`)."""
    monkeypatch.setattr(strategy, "_get_htf_bias", lambda df: htf_bias)
    monkeypatch.setattr(strategy, "_get_ltf_bias", lambda df: ltf_bias)
    return strategy.evaluate(
        htf_df=_flat(60),
        bias_df=_flat(30),
        poi_df=_flat(40),
        entry_df=_flat(30),
        session=_session(),
    )


# ---- the four canonical tests --------------------------------------------


def test_aligned_bullish_fires_buy(monkeypatch):
    _patch_all_gates_pass(monkeypatch, htf_bias="BULLISH", ltf_bias="BULLISH")
    s = ICTProMaxStrategy(require_bias_alignment=True, require_fvg=False)
    out = _run_strategy(s, "BULLISH", "BULLISH", monkeypatch)
    assert out["entry"] == "BUY", out.get("diagnostics")


def test_aligned_bearish_fires_sell(monkeypatch):
    _patch_all_gates_pass(monkeypatch, htf_bias="BEARISH", ltf_bias="BEARISH")
    s = ICTProMaxStrategy(require_bias_alignment=True, require_fvg=False)
    out = _run_strategy(s, "BEARISH", "BEARISH", monkeypatch)
    assert out["entry"] == "SELL", out.get("diagnostics")


def test_misaligned_blocks_with_blocker(monkeypatch):
    """HTF=BEARISH, LTF=BULLISH — the live failure pattern. Gate must
    refuse the SELL and surface the mismatch in diagnostics."""
    _patch_all_gates_pass(monkeypatch, htf_bias="BEARISH", ltf_bias="BULLISH")
    s = ICTProMaxStrategy(require_bias_alignment=True, require_fvg=False)
    out = _run_strategy(s, "BEARISH", "BULLISH", monkeypatch)
    assert out["entry"] == "NO ENTRY"
    blockers = out["diagnostics"]["blockers"]
    assert any("Bias mismatch" in b for b in blockers), f"missing mismatch in {blockers}"
    assert any("HTF=BEARISH" in b and "LTF=BULLISH" in b for b in blockers)


def test_misaligned_passes_when_gate_off(monkeypatch):
    """Regression guard: with require_bias_alignment=False, the same
    misaligned setup still fires (pre-Phase-E behaviour)."""
    _patch_all_gates_pass(monkeypatch, htf_bias="BEARISH", ltf_bias="BULLISH")
    s = ICTProMaxStrategy(require_bias_alignment=False, require_fvg=False)
    out = _run_strategy(s, "BEARISH", "BULLISH", monkeypatch)
    assert out["entry"] == "SELL", out.get("diagnostics")


# ---- isolated diagnose tests ---------------------------------------------


def test_diagnose_emits_mismatch_blocker_on_both_sides():
    """_diagnose adds the mismatch blocker to both buy and sell lists
    when require_bias_alignment=True and biases disagree."""
    diag = _diagnose(
        htf_bias="BEARISH",
        poi_tap="POI TAPPED",
        ltf_mss="BEARISH MSS",
        micro_fvg="BEARISH FVG",
        delta=-1.0,
        require_fvg=True,
        ltf_bias="BULLISH",
        require_bias_alignment=True,
    )
    assert any("Bias mismatch" in b for b in diag["buy_blockers"])
    assert any("Bias mismatch" in b for b in diag["sell_blockers"])


def test_diagnose_skips_blocker_when_gate_off():
    diag = _diagnose(
        htf_bias="BEARISH",
        poi_tap="POI TAPPED",
        ltf_mss="BEARISH MSS",
        micro_fvg="BEARISH FVG",
        delta=-1.0,
        require_fvg=True,
        ltf_bias="BULLISH",
        require_bias_alignment=False,
    )
    assert not any("Bias mismatch" in b for b in diag["buy_blockers"])
    assert not any("Bias mismatch" in b for b in diag["sell_blockers"])


def test_diagnose_skips_blocker_when_ltf_unknown():
    """If ltf_bias is 'N/A' (e.g. caller didn't compute it), don't pollute
    the blocker list with a meaningless mismatch."""
    diag = _diagnose(
        htf_bias="BEARISH",
        poi_tap="POI TAPPED",
        ltf_mss="BEARISH MSS",
        micro_fvg="BEARISH FVG",
        delta=-1.0,
        require_fvg=True,
        ltf_bias="N/A",
        require_bias_alignment=True,
    )
    assert not any("Bias mismatch" in b for b in diag["buy_blockers"])


# ---- funnel-step mapping --------------------------------------------------


def test_scanner_blocker_to_step_maps_mismatch_to_bias_align():
    from ictbot.orchestrator.scanner import _STEP_ORDER, _blocker_to_step

    assert "bias_align" in _STEP_ORDER
    # Ordering: bias_align must come AFTER htf_bias (a bias-alignment
    # failure presupposes the HTF call is well-defined) but before the
    # downstream POI/MSS gates.
    assert _STEP_ORDER.index("bias_align") > _STEP_ORDER.index("htf_bias")
    assert _STEP_ORDER.index("bias_align") < _STEP_ORDER.index("poi_tap")

    assert _blocker_to_step("Bias mismatch: HTF=BEARISH vs LTF=BULLISH") == "bias_align"
    # Other blockers stay untouched.
    assert _blocker_to_step("HTF bias is BEARISH (need BULLISH)") == "htf_bias"
