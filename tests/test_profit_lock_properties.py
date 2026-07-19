"""Property-based invariants for the profit-lock ratchet.

The hand-written cases pin specific transitions; these assert the invariants that
must hold for ALL inputs — the ratchet decision (`_profit_lock_eval`) and the sweep
replay (`campaign_outcome`) should never violate them regardless of the equity path
or the (validly-ordered) campaign parameters.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

REPO = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RA = _load("run_allocator_props", "scripts/run_allocator.py")
SC = _load("sweep_campaign_props", "scripts/sweep_campaign.py")


# Valid campaign params, always ordered 0 < min_keep < trigger < bank < 1, 0 < trail < 1.
@st.composite
def params(draw):
    min_keep = draw(st.floats(min_value=0.005, max_value=0.05))
    trigger = draw(st.floats(min_value=min_keep + 0.005, max_value=0.15))
    bank = draw(st.floats(min_value=trigger + 0.005, max_value=0.40))
    trail = draw(st.floats(min_value=0.005, max_value=0.20))
    return dict(trigger=trigger, trail=trail, min_keep=min_keep, bank=bank)


_NAV = st.floats(min_value=1.0, max_value=10_000.0)
_ANCHOR = st.floats(min_value=100.0, max_value=5_000.0)


# ------------------------------ _profit_lock_eval -------------------------- #
@given(
    anchor=_ANCHOR,
    nav=_NAV,
    p=params(),
    armed=st.booleans(),
    peak=st.floats(min_value=1.0, max_value=12_000.0),
)
@settings(max_examples=300, deadline=None)
def test_eval_never_raises_and_action_valid(anchor, nav, p, armed, peak):
    state = {"campaign_start_nav": anchor}
    if armed:
        state["profit_lock_armed"] = True
        state["peak_since_trigger"] = peak
    action, upd = RA._profit_lock_eval(state, nav, **p)
    assert action in ("none", "arm", "bank", "trail")
    assert isinstance(upd, dict)


@given(anchor=_ANCHOR, nav=_NAV, p=params())
@settings(max_examples=300, deadline=None)
def test_eval_arm_floor_respects_min_keep(anchor, nav, p):
    action, upd = RA._profit_lock_eval({"campaign_start_nav": anchor}, nav, **p)
    if action == "arm":
        assert upd["lock_floor"] >= anchor * (1.0 + p["min_keep"]) - 1e-6


@given(anchor=_ANCHOR, nav=_NAV, p=params(), peak=st.floats(min_value=1.0, max_value=12_000.0))
@settings(max_examples=300, deadline=None)
def test_eval_armed_floor_always_at_least_min_keep(anchor, nav, p, peak):
    state = {"campaign_start_nav": anchor, "profit_lock_armed": True, "peak_since_trigger": peak}
    _, upd = RA._profit_lock_eval(state, nav, **p)
    if "lock_floor" in upd:
        assert upd["lock_floor"] >= anchor * (1.0 + p["min_keep"]) - 1e-6


@given(anchor=_ANCHOR, nav=_NAV, p=params())
@settings(max_examples=300, deadline=None)
def test_eval_bank_only_at_or_above_bank_threshold(anchor, nav, p):
    action, _ = RA._profit_lock_eval({"campaign_start_nav": anchor}, nav, **p)
    if action == "bank":
        assert nav / anchor - 1.0 >= p["bank"] - 1e-9


@given(anchor=_ANCHOR, nav=_NAV, p=params())
@settings(max_examples=300, deadline=None)
def test_eval_noop_below_trigger_when_not_armed(anchor, nav, p):
    action, _ = RA._profit_lock_eval({"campaign_start_nav": anchor}, nav, **p)
    if nav / anchor - 1.0 < p["trigger"]:
        assert action in ("none", "bank")  # bank only if >= bank > trigger (impossible here)
        assert action == "none"


@given(nav=_NAV, p=params())
@settings(max_examples=100, deadline=None)
def test_eval_missing_or_zero_anchor_is_noop(nav, p):
    assert RA._profit_lock_eval({}, nav, **p) == ("none", {})
    assert RA._profit_lock_eval({"campaign_start_nav": 0.0}, nav, **p) == ("none", {})


@given(anchor=_ANCHOR, p=params(), peak=st.floats(min_value=1.0, max_value=12_000.0), nav=_NAV)
@settings(max_examples=300, deadline=None)
def test_eval_peak_never_decreases_when_armed(anchor, p, peak, nav):
    state = {"campaign_start_nav": anchor, "profit_lock_armed": True, "peak_since_trigger": peak}
    _, upd = RA._profit_lock_eval(state, nav, **p)
    if "peak_since_trigger" in upd:
        assert upd["peak_since_trigger"] >= peak - 1e-9
        assert upd["peak_since_trigger"] >= nav - 1e-9


# ------------------------------ campaign_outcome --------------------------- #
@st.composite
def equity(draw):
    n = draw(st.integers(min_value=2, max_value=40))
    vals = draw(st.lists(st.floats(min_value=1.0, max_value=1e6), min_size=n, max_size=n))
    return np.asarray(vals, dtype=float)


@given(eq=equity(), dd_cap=st.floats(min_value=0.05, max_value=0.30), p=params())
@settings(max_examples=400, deadline=None)
def test_campaign_outcome_invariants(eq, dd_cap, p):
    out, status, rdd = SC.campaign_outcome(eq, dd_cap=dd_cap, **p)
    assert status in ("end", "bank", "trail", "halt")
    assert 0.0 <= rdd < 1.0
    assert np.isfinite(out)
    if status == "halt":
        assert rdd > dd_cap  # halt ⇒ realized drawdown over the cap
    if status == "bank":
        assert out >= p["bank"] - 1e-9  # bank ⇒ outcome reached the bank level
    if status == "end":
        assert rdd <= dd_cap + 1e-9  # never breached the cap (else it'd halt)
        assert out < p["bank"] + 1e-9  # never reached bank (else it'd bank)
