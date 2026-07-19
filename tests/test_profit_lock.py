"""Profit-lock ratchet (PnL campaign): pure eval + tick/watch wiring + CLI.

Mirrors the importlib + tmp-redirect pattern of test_run_allocator_hardening.py.
All sim-only — no network, no real swaps.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_allocator.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_allocator_profit_lock", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ra(tmp_path, monkeypatch):
    """run_allocator module with state/journal paths redirected into tmp."""
    mod = _load()
    monkeypatch.setattr(mod, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(mod, "SIM_JOURNAL", tmp_path / "allocator_journal.jsonl")
    monkeypatch.setattr(mod, "LIVE_JOURNAL", tmp_path / "allocator_live.jsonl")
    monkeypatch.setattr(mod, "SIM_STATE", tmp_path / "allocator_state.json")
    monkeypatch.setattr(mod, "LIVE_STATE", tmp_path / "allocator_live_state.json")
    return mod


def _wire(ra, monkeypatch, *, fg=55):
    """Mock the CMC/price feeds so a full sim tick runs offline + deterministically."""

    def fake_fetch(sym, limit=2500):
        n = 400
        t = pd.date_range("2026-05-01", periods=n, freq="4h", tz="UTC")
        close = 100.0 + 0.3 * np.arange(n)  # gentle uptrend
        return pd.DataFrame(
            {
                "time": t,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1.0,
            }
        )

    monkeypatch.setattr(ra, "fetch_4h", fake_fetch)
    monkeypatch.setattr(ra, "fear_greed", lambda *a, **k: fg)
    monkeypatch.setattr(ra, "price_fn", lambda *a, **k: lambda tok: 1.0 if tok == "USDT" else 100.0)
    monkeypatch.setattr(ra.settings, "x402_enabled", False, raising=False)
    monkeypatch.setattr(ra.settings, "agent_heartbeat_enabled", False, raising=False)
    # Pin the min-swap floor so the "can't fund a sliver" cases are deterministic regardless of the
    # local .env (live arming sets ALLOC_MIN_SWAP_USD=0.5, which makes a $0.5 seed *exactly* fundable
    # → the nudge banks instead of failing). A fixed 1.0 floor keeps $0.5 unfundable as the tests intend.
    monkeypatch.setattr(ra.settings, "alloc_min_swap_usd", 1.0, raising=False)


def _enable(ra, monkeypatch, *, trigger=0.05, trail=0.03, min_keep=0.03, bank=0.10):
    monkeypatch.setattr(ra.settings, "profit_lock_enabled", True, raising=False)
    monkeypatch.setattr(ra.settings, "profit_lock_trigger", trigger, raising=False)
    monkeypatch.setattr(ra.settings, "profit_lock_trail", trail, raising=False)
    monkeypatch.setattr(ra.settings, "profit_lock_min_keep", min_keep, raising=False)
    monkeypatch.setattr(ra.settings, "profit_lock_bank", bank, raising=False)


def _rows(tmp_path, name="allocator_journal.jsonl"):
    jf = tmp_path / name
    if not jf.exists():
        return []
    return [json.loads(l) for l in jf.read_text().splitlines() if l.strip()]


def _seed(
    ra, *, balances, hwm=None, anchor=None, armed=False, locked=False, peak=None, halted=False
):
    st = {
        "hwm": hwm,
        "halted": halted,
        "balances": balances,
        "cumulative_swaps": 0,
        "window_start_ts": None,
    }
    if anchor is not None:
        st["campaign_start_nav"] = anchor
    if armed:
        st["profit_lock_armed"] = True
    if locked:
        st["profit_locked"] = True
    if peak is not None:
        st["peak_since_trigger"] = peak
    ra.save_state(st, "sim")


KW = dict(trigger=0.05, trail=0.03, min_keep=0.03, bank=0.10)


# ------------------------------ pure eval ---------------------------------- #
def test_eval_noop_below_trigger(ra):
    action, upd = ra._profit_lock_eval({"campaign_start_nav": 1000.0}, 1030.0, **KW)
    assert (action, upd) == ("none", {})


def test_eval_noop_without_anchor(ra):
    assert ra._profit_lock_eval({}, 1100.0, **KW) == ("none", {})
    assert ra._profit_lock_eval({"campaign_start_nav": 0.0}, 1100.0, **KW) == ("none", {})


def test_eval_arms_at_trigger(ra):
    action, upd = ra._profit_lock_eval({"campaign_start_nav": 1000.0}, 1050.0, **KW)
    assert action == "arm"
    assert upd["profit_lock_armed"] is True
    assert upd["peak_since_trigger"] == 1050.0
    # floor = max(anchor*1.03, nav*0.97) = max(1030, 1018.5)
    assert upd["lock_floor"] == pytest.approx(1030.0)


def test_eval_bank_at_bank_level(ra):
    action, upd = ra._profit_lock_eval({"campaign_start_nav": 1000.0}, 1100.0, **KW)
    assert action == "bank"
    assert upd["peak_since_trigger"] == 1100.0


def test_eval_trail_fires_after_giveback(ra):
    st = {"campaign_start_nav": 1000.0, "profit_lock_armed": True, "peak_since_trigger": 1080.0}
    # floor = max(1030, 1080*0.97=1047.6); nav 1047 < floor -> trail
    action, upd = ra._profit_lock_eval(st, 1047.0, **KW)
    assert action == "trail"
    assert upd["lock_floor"] == pytest.approx(1047.6)
    assert upd["peak_since_trigger"] == 1080.0


def test_eval_lock_floor_respects_min_keep(ra):
    # peak barely above trigger: trail floor (1023.35) is BELOW min_keep (1030)
    st = {"campaign_start_nav": 1000.0, "profit_lock_armed": True, "peak_since_trigger": 1055.0}
    action, _ = ra._profit_lock_eval(st, 1031.0, **KW)
    assert action == "none"  # above the 1030 min-keep floor
    action, upd = ra._profit_lock_eval(st, 1029.0, **KW)
    assert action == "trail"  # below it -> lock
    assert upd["lock_floor"] == pytest.approx(1030.0)


def test_eval_peak_ratchets_monotonically(ra):
    st = {"campaign_start_nav": 1000.0, "profit_lock_armed": True, "peak_since_trigger": 1080.0}
    _, upd = ra._profit_lock_eval(st, 1060.0, **KW)
    assert upd["peak_since_trigger"] == 1080.0  # never decreases
    _, upd = ra._profit_lock_eval(st, 1090.0, **KW)
    assert upd["peak_since_trigger"] == 1090.0  # rises with a new high


def test_state_roundtrip_campaign_fields(ra):
    st = {
        "hwm": 1050.0,
        "halted": False,
        "balances": {"USDT": 1050.0},
        "cumulative_swaps": 3,
        "window_start_ts": None,
        "campaign_start_nav": 1000.0,
        "profit_lock_armed": True,
        "peak_since_trigger": 1052.5,
        "lock_floor": 1030.0,
        "profit_locked": False,
    }
    ra.save_state(st, "sim")
    assert ra.load_state("sim") == st


# ------------------------------ CLI one-shots ------------------------------ #
def test_anchor_cli_writes_state_and_journal(ra, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--anchor-nav", "1000"])
    assert ra.main() == 0
    assert ra.load_state("sim")["campaign_start_nav"] == 1000.0
    rows = _rows(tmp_path)
    assert rows[-1]["event"] == "CAMPAIGN_ANCHOR" and rows[-1]["source"] == "cli"


def test_unlock_profit_clears_lock_keeps_anchor(ra, tmp_path, monkeypatch):
    _seed(ra, balances={"USDT": 1100.0}, anchor=1000.0, armed=True, locked=True, peak=1110.0)
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--unlock-profit"])
    assert ra.main() == 0
    st = ra.load_state("sim")
    assert st["profit_locked"] is False and st["profit_lock_armed"] is False
    assert "peak_since_trigger" not in st
    assert st["campaign_start_nav"] == 1000.0  # anchor survives the unlock
    assert _rows(tmp_path)[-1]["event"] == "PROFIT_UNLOCK"


def test_resume_does_not_clear_profit_lock(ra, monkeypatch):
    _seed(ra, balances={"USDT": 1100.0}, anchor=1000.0, locked=True, halted=True)
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--resume"])
    assert ra.main() == 0
    st = ra.load_state("sim")
    assert st["halted"] is False
    assert st["profit_locked"] is True  # --resume never re-opens a banked campaign


# ------------------------------ full sim tick ------------------------------ #
def test_tick_arms_at_trigger(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1000.0}, anchor=950.0)  # cum = +5.26%
    assert ra.tick("sim", 0.10) == 0  # arms, then rebalances
    st = ra.load_state("sim")
    assert st["profit_lock_armed"] is True and not st.get("profit_locked")
    events = [r["event"] for r in _rows(tmp_path)]
    assert "PROFIT_LOCK_ARMED" in events and "REBALANCE" in events
    reb = [r for r in _rows(tmp_path) if r["event"] == "REBALANCE"][-1]
    assert reb["profit_lock"]["armed"] is True
    assert "Profit lock armed" in reb["rationale"]


def test_tick_banks_at_target(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    # NAV = 100 USDT + 9 BNB x $100 = 1000; anchor 900 -> cum +11.1% >= bank
    _seed(ra, balances={"USDT": 100.0, "BNB": 9.0}, anchor=900.0)
    assert ra.tick("sim", 0.10) == 1
    st = ra.load_state("sim")
    assert st["profit_locked"] is True
    assert st["balances"].get("BNB", 0.0) == pytest.approx(0.0, abs=1e-9)  # flattened
    last = [r for r in _rows(tmp_path) if r["event"] == "PROFIT_LOCK"][-1]
    assert last["kind"] == "bank" and last["source"] == "daily_tick"
    assert last["flattened"] >= 1
    assert not any(r["event"] == "REBALANCE" for r in _rows(tmp_path))


def test_tick_trails_after_giveback(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    # armed at peak 1040 (anchor 940): floor = max(968.2, 1008.8); NAV 1000 < floor
    _seed(ra, balances={"USDT": 1000.0}, anchor=940.0, armed=True, peak=1040.0)
    assert ra.tick("sim", 0.10) == 1
    last = [r for r in _rows(tmp_path) if r["event"] == "PROFIT_LOCK"][-1]
    assert last["kind"] == "trail"
    assert ra.load_state("sim")["profit_locked"] is True


def test_tick_dd_halt_outranks_profit_lock(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    # HWM far above NAV forces the DD halt even though cum-vs-anchor looks bank-worthy.
    _seed(ra, balances={"USDT": 1000.0}, anchor=100.0, hwm=10_000.0)
    assert ra.tick("sim", 0.10) == 1
    events = [r["event"] for r in _rows(tmp_path)]
    assert "DD_HALT" in events and "PROFIT_LOCK" not in events
    st = ra.load_state("sim")
    assert st["halted"] is True and not st.get("profit_locked")


def test_locked_tick_skips_rebalance(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1100.0}, anchor=1000.0, locked=True)
    assert ra.tick("sim", 0.10) == 0
    assert not any(r["event"] == "REBALANCE" for r in _rows(tmp_path))


def test_locked_tick_still_banks_floor_nudges_in_window(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1100.0}, anchor=1000.0, locked=True)
    monkeypatch.setattr(ra, "_trade_floor_shortfall", lambda cum, now=None: 2)
    assert ra.tick("sim", 0.10) == 0
    nudges = [r for r in _rows(tmp_path) if r["event"] == "FLOOR_NUDGE"]
    assert nudges and nudges[-1]["banked"] >= 2
    assert ra.load_state("sim")["cumulative_swaps"] >= 2
    assert not any(r["event"] == "REBALANCE" for r in _rows(tmp_path))


def test_tick_disabled_flag_writes_no_campaign_keys(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    monkeypatch.setattr(ra.settings, "profit_lock_enabled", False, raising=False)
    _seed(ra, balances={"USDT": 1000.0})
    assert ra.tick("sim", 0.10) == 0
    st = ra.load_state("sim")
    for key in (
        "campaign_start_nav",
        "profit_lock_armed",
        "profit_locked",
        "peak_since_trigger",
        "lock_floor",
    ):
        assert key not in st, f"disabled run leaked state key {key}"
    reb = [r for r in _rows(tmp_path) if r["event"] == "REBALANCE"][-1]
    assert reb["profit_lock"] is None


def test_tick_self_inits_anchor_with_audit_row(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1000.0})  # enabled but NO anchor set
    assert ra.tick("sim", 0.10) == 0
    anchors = [r for r in _rows(tmp_path) if r["event"] == "CAMPAIGN_ANCHOR"]
    assert len(anchors) == 1 and anchors[0]["source"] == "self_init"
    assert ra.load_state("sim")["campaign_start_nav"] == pytest.approx(1000.0)


# ------------------------------ dd-watch (intraday) ------------------------ #
def test_dd_watch_arms_intraday(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1000.0}, anchor=950.0, hwm=1000.0)
    assert ra.dd_watch("sim", 0.10) == 0
    st = ra.load_state("sim")
    assert st["profit_lock_armed"] is True and not st.get("profit_locked")
    last = [r for r in _rows(tmp_path) if r["event"] == "PROFIT_LOCK_ARMED"][-1]
    assert last["source"] == "dd_watch"


def test_dd_watch_banks_intraday(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 100.0, "BNB": 9.0}, anchor=900.0, hwm=1000.0)
    assert ra.dd_watch("sim", 0.10) == 1
    st = ra.load_state("sim")
    assert st["profit_locked"] is True
    assert st["balances"].get("BNB", 0.0) == pytest.approx(0.0, abs=1e-9)
    last = [r for r in _rows(tmp_path) if r["event"] == "PROFIT_LOCK"][-1]
    assert last["source"] == "dd_watch" and last["kind"] == "bank"


def test_dd_watch_skips_when_locked(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1100.0}, anchor=1000.0, locked=True, hwm=1100.0)
    assert ra.dd_watch("sim", 0.10) == 0
    assert not any(r["event"] in ("PROFIT_LOCK", "DD_HALT") for r in _rows(tmp_path))


def test_dd_watch_never_self_inits_anchor(ra, tmp_path, monkeypatch):
    # The watcher must not seed the campaign anchor (the tick's job) — mirroring
    # its no-HWM-seeding contract.
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1000.0}, hwm=1000.0)  # no anchor
    assert ra.dd_watch("sim", 0.10) == 0
    assert "campaign_start_nav" not in ra.load_state("sim")
    assert not any(r["event"] == "CAMPAIGN_ANCHOR" for r in _rows(tmp_path))


def test_dd_watch_disabled_is_noop(ra, tmp_path, monkeypatch):
    _wire(ra, monkeypatch)
    monkeypatch.setattr(ra.settings, "profit_lock_enabled", False, raising=False)
    _seed(ra, balances={"USDT": 1000.0}, anchor=900.0, hwm=1000.0)  # bank-worthy if enabled
    assert ra.dd_watch("sim", 0.10) == 0
    assert not ra.load_state("sim").get("profit_locked")
    assert not any(r["event"].startswith("PROFIT_LOCK") for r in _rows(tmp_path))


# ------------------------------ boundary conditions ------------------------ #
def test_eval_nav_exactly_at_lock_floor_is_safe(ra):
    # the trail decision is strict `nav < floor`; nav == floor must NOT trail
    st = {"campaign_start_nav": 1000.0, "profit_lock_armed": True, "peak_since_trigger": 1080.0}
    floor = max(1000.0 * 1.03, 1080.0 * 0.97)  # = 1047.6
    assert ra._profit_lock_eval(st, floor, **KW)[0] == "none"
    assert ra._profit_lock_eval(st, floor - 0.01, **KW)[0] == "trail"


def test_eval_cum_exactly_at_trigger_arms(ra):
    # `cum >= trigger` — exactly +5% arms
    assert ra._profit_lock_eval({"campaign_start_nav": 1000.0}, 1050.0, **KW)[0] == "arm"


def test_eval_cum_exactly_at_bank_banks(ra):
    # `cum >= bank` — exactly +10% banks
    assert ra._profit_lock_eval({"campaign_start_nav": 1000.0}, 1100.0, **KW)[0] == "bank"


# ------------------------------ anchor lifecycle --------------------------- #
def test_reset_wipes_anchor_then_reanchor(ra, tmp_path, monkeypatch):
    _seed(ra, balances={"USDT": 1100.0}, anchor=1000.0, armed=True, peak=1100.0)
    # --reset wipes the whole state (anchor included)
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--reset"])
    assert ra.main() == 0
    assert "campaign_start_nav" not in ra.load_state("sim")
    # re-anchor restores a clean campaign baseline
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--anchor-nav", "1000"])
    assert ra.main() == 0
    st = ra.load_state("sim")
    assert st["campaign_start_nav"] == 1000.0
    assert not st.get("profit_lock_armed")  # fresh — the prior armed flag is gone


def test_anchor_nav_twice_latest_wins(ra, monkeypatch):
    for v in ("1000", "1234.5"):
        monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--anchor-nav", v])
        assert ra.main() == 0
    assert ra.load_state("sim")["campaign_start_nav"] == 1234.5


def test_anchor_nav_rejects_nonpositive(ra, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--anchor-nav", "0"])
    assert ra.main() == 2  # refused, no state written
    assert "campaign_start_nav" not in ra.load_state("sim")


# ------------------------------ profit_lock dict variants on REBALANCE ----- #
def test_rebalance_profit_lock_dict_watching_state(ra, tmp_path, monkeypatch):
    # enabled + anchor set but below trigger -> "watching" sub-dict (armed/locked False)
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1000.0}, anchor=1000.0)  # cum 0% < trigger
    assert ra.tick("sim", 0.10) == 0
    reb = [r for r in _rows(tmp_path) if r["event"] == "REBALANCE"][-1]
    pl = reb["profit_lock"]
    assert pl["enabled"] is True and pl["armed"] is False and pl["locked"] is False
    assert pl["campaign_start_nav"] == 1000.0
    assert pl["peak_since_trigger"] is None and pl["lock_floor"] is None


def test_locked_tick_journals_floor_nudge_failed_when_cannot_bank(ra, tmp_path, monkeypatch):
    # locked book + a shortfall but no USDT to fund a sliver -> FLOOR_NUDGE_FAILED, rc 0
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, balances={"USDT": 0.5}, anchor=1000.0, locked=True)
    monkeypatch.setattr(ra, "_trade_floor_shortfall", lambda cum, now=None: 2)
    assert ra.tick("sim", 0.10) == 0
    failed = [r for r in _rows(tmp_path) if r["event"] == "FLOOR_NUDGE_FAILED"]
    assert failed and failed[-1]["need"] == 2
    assert not any(r["event"] == "REBALANCE" for r in _rows(tmp_path))
