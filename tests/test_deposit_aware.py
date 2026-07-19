"""
Deposit-aware re-baselining (scripts/run_allocator.py).

The live book is tiny (~$8) and the operator periodically tops up native BNB for gas.
A deposit inflates NAV = USDT + Σ(token×price); without correction it is counted as
trading PnL and a deposit-inflated NAV has crossed the profit-lock `bank` threshold and
flattened the book. The guard treats a MATERIAL external balance drift as CAPITAL: it
shifts the campaign anchor + HWM by the net USD flow (so PnL stays trading-only) and skips
that tick's trade/profit-lock/DD eval (re-baseline only, never trade on a deposit tick).

The script lives in scripts/ (not the package), so we load it via importlib — same pattern
as test_run_allocator_hardening.py.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_allocator.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_allocator_deposit", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ra(tmp_path, monkeypatch):
    """run_allocator with state + journal paths redirected into a tmp dir (live track)."""
    mod = _load()
    monkeypatch.setattr(mod, "LIVE_STATE", tmp_path / "allocator_live_state.json")
    monkeypatch.setattr(mod, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(mod, "LIVE_JOURNAL", tmp_path / "allocator_live.jsonl")
    return mod


class _FakeClient:
    """On-chain client returning a fixed balance snapshot (post-transfer truth)."""

    def __init__(self, bals):
        self._b = dict(bals)

    def balances(self):
        return dict(self._b)


def _journal_rows(tmp_path):
    jf = tmp_path / "allocator_live.jsonl"
    if not jf.exists():
        return []
    return [json.loads(line) for line in jf.read_text().splitlines() if line.strip()]


# --------------------------- _net_external_flow (pure) --------------------------- #
def test_net_flow_deposit_priced(ra):
    # +0.002 BNB @ $575 = +$1.15 inbound
    flow = ra._net_external_flow({"BNB": {"expected": 0.001, "actual": 0.003}}, {"BNB": 575.0})
    assert flow == pytest.approx(1.15, abs=1e-6)


def test_net_flow_usdt_at_par(ra):
    # USDT is valued at $1 regardless of a price map entry
    assert ra._net_external_flow({"USDT": {"expected": 2.0, "actual": 3.5}}, {}) == pytest.approx(1.5)


def test_net_flow_withdrawal_negative(ra):
    flow = ra._net_external_flow({"BNB": {"expected": 0.003, "actual": 0.001}}, {"BNB": 575.0})
    assert flow == pytest.approx(-1.15, abs=1e-6)


def test_net_flow_none_and_empty_are_zero(ra):
    assert ra._net_external_flow(None, {}) == 0.0
    assert ra._net_external_flow({}, {"BNB": 575.0}) == 0.0


def test_net_flow_unknown_price_contributes_zero(ra):
    # a token with no price in the map can't be valued -> 0 contribution (never NaN/raise)
    assert ra._net_external_flow({"FOO": {"expected": 0.0, "actual": 5.0}}, {}) == 0.0


# --------------------------- _apply_capital_flow (action) --------------------------- #
def test_deposit_reanchors_hwm_and_skips(ra, tmp_path, monkeypatch):
    monkeypatch.setattr(ra.settings, "alloc_deposit_aware", True)
    state = {"campaign_start_nav": 8.0, "hwm": 8.0, "balances": {"USDT": 3.0, "BNB": 0.001}}
    drift = {"BNB": {"expected": 0.001, "actual": 0.003}}  # +0.002 BNB
    prices = {"BNB": 575.0, "USDT": 1.0}
    client = _FakeClient({"USDT": 3.0, "BNB": 0.003})
    nav = 3.0 + 0.003 * 575.0  # 4.725

    handled = ra._apply_capital_flow(state, drift, prices, nav, client, "live")

    assert handled is True  # caller will skip the tick
    # anchor + HWM both shift up by the inflow (+$1.15) -> PnL stays trading-only
    assert state["campaign_start_nav"] == pytest.approx(9.15, abs=1e-6)
    assert state["hwm"] == pytest.approx(9.15, abs=1e-6)
    assert state["balances"] == {"USDT": 3.0, "BNB": 0.003}  # refreshed from chain
    # persisted to disk
    assert ra.load_state("live")["campaign_start_nav"] == pytest.approx(9.15, abs=1e-6)
    # a single CAPITAL_FLOW row journaled
    cf = [r for r in _journal_rows(tmp_path) if r["event"] == "CAPITAL_FLOW"]
    assert len(cf) == 1
    assert cf[0]["kind"] == "deposit"
    assert cf[0]["net_flow_usd"] == pytest.approx(1.15, abs=1e-4)
    assert cf[0]["new_anchor"] == pytest.approx(9.15, abs=1e-6)


def test_withdrawal_lowers_anchor(ra, tmp_path, monkeypatch):
    monkeypatch.setattr(ra.settings, "alloc_deposit_aware", True)
    state = {"campaign_start_nav": 9.0, "hwm": 9.0, "balances": {"USDT": 3.0, "BNB": 0.003}}
    drift = {"BNB": {"expected": 0.003, "actual": 0.001}}  # -0.002 BNB outbound
    client = _FakeClient({"USDT": 3.0, "BNB": 0.001})

    handled = ra._apply_capital_flow(state, drift, {"BNB": 575.0}, 4.0, client, "live")

    assert handled is True
    assert state["campaign_start_nav"] == pytest.approx(9.0 - 1.15, abs=1e-6)
    assert state["hwm"] == pytest.approx(9.0 - 1.15, abs=1e-6)
    cf = [r for r in _journal_rows(tmp_path) if r["event"] == "CAPITAL_FLOW"]
    assert cf and cf[0]["kind"] == "withdrawal"


def test_subthreshold_drift_is_noop(ra, monkeypatch):
    # +0.0003 BNB = +$0.1725 < materiality floor max($0.25, 1%·NAV) -> not treated as capital
    monkeypatch.setattr(ra.settings, "alloc_deposit_aware", True)
    state = {"campaign_start_nav": 8.0, "hwm": 8.0, "balances": {"USDT": 3.0, "BNB": 0.001}}
    drift = {"BNB": {"expected": 0.001, "actual": 0.0013}}
    handled = ra._apply_capital_flow(state, drift, {"BNB": 575.0}, 8.0, _FakeClient({}), "live")
    assert handled is False
    assert state["campaign_start_nav"] == 8.0 and state["hwm"] == 8.0  # untouched


def test_disabled_flag_is_noop(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "alloc_deposit_aware", False)
    state = {"campaign_start_nav": 8.0, "hwm": 8.0, "balances": {"USDT": 3.0, "BNB": 0.001}}
    drift = {"BNB": {"expected": 0.001, "actual": 0.003}}  # would be material if enabled
    handled = ra._apply_capital_flow(state, drift, {"BNB": 575.0}, 5.0, _FakeClient({}), "live")
    assert handled is False
    assert state["campaign_start_nav"] == 8.0  # bit-for-bit baseline when off


def test_no_anchor_does_not_crash(ra, monkeypatch):
    # before the campaign is anchored, a flow still shouldn't crash; anchor stays unset
    monkeypatch.setattr(ra.settings, "alloc_deposit_aware", True)
    state = {"balances": {"USDT": 3.0, "BNB": 0.001}}  # no campaign_start_nav / hwm
    drift = {"BNB": {"expected": 0.001, "actual": 0.003}}
    handled = ra._apply_capital_flow(state, drift, {"BNB": 575.0}, 4.7, _FakeClient({"USDT": 3.0}), "live")
    assert handled is True
    assert "campaign_start_nav" not in state  # nothing to shift; no crash
