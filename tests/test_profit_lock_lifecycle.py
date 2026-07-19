"""Multi-tick lifecycle tests for the profit-lock ratchet.

The single-tick tests in test_profit_lock.py check each transition in isolation;
these drive the REAL `tick()` / `dd_watch()` across a SEQUENCE of ticks — the way
the live agent runs — to prove the state machine (none → arm → ratchet → bank/trail,
plus unlock → re-arm) holds across persisted ticks.

Technique: stub `TwakSpotBroker.nav` with a mutable holder set BEFORE each tick, so
every nav read within a tick is the same scripted value and the ratchet is exercised
independent of rebalance mechanics. Loaded via importlib (same pattern as the others).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_allocator.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_allocator_lifecycle", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ra(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(mod, "SIM_JOURNAL", tmp_path / "allocator_journal.jsonl")
    monkeypatch.setattr(mod, "LIVE_JOURNAL", tmp_path / "allocator_live.jsonl")
    monkeypatch.setattr(mod, "SIM_STATE", tmp_path / "allocator_state.json")
    monkeypatch.setattr(mod, "LIVE_STATE", tmp_path / "allocator_live_state.json")
    return mod


@pytest.fixture
def nav_holder(ra, monkeypatch):
    """Stub broker.nav so every read in a tick returns holder['nav']; advance it
    between ticks to script an equity trajectory."""
    holder = {"nav": 1000.0}
    monkeypatch.setattr(ra.TwakSpotBroker, "nav", lambda self, prices: holder["nav"])
    return holder


def _wire(ra, monkeypatch, *, fg=55):
    def fake_fetch(sym, limit=2500):
        n = 400
        t = pd.date_range("2026-05-01", periods=n, freq="4h", tz="UTC")
        close = 100.0 + 0.3 * np.arange(n)
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


def _enable(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "profit_lock_enabled", True, raising=False)
    monkeypatch.setattr(ra.settings, "profit_lock_trigger", 0.05, raising=False)
    monkeypatch.setattr(ra.settings, "profit_lock_trail", 0.03, raising=False)
    monkeypatch.setattr(ra.settings, "profit_lock_min_keep", 0.03, raising=False)
    monkeypatch.setattr(ra.settings, "profit_lock_bank", 0.10, raising=False)


def _seed(ra, *, anchor=1000.0, balances=None, hwm=None, locked=False):
    st = {
        "hwm": hwm,
        "halted": False,
        "balances": balances or {"USDT": 600.0, "BNB": 4.0},
        "cumulative_swaps": 0,
        "window_start_ts": None,
        "campaign_start_nav": anchor,
    }
    if locked:
        st["profit_locked"] = True
    ra.save_state(st, "sim")


def _rows(ra):
    jf = ra.SIM_JOURNAL
    if not jf.exists():
        return []
    return [json.loads(l) for l in jf.read_text().splitlines() if l.strip()]


def _events(ra):
    return [r["event"] for r in _rows(ra)]


# ------------------------------ arm → ratchet → bank ----------------------- #
def test_lifecycle_arm_ratchet_then_bank(ra, monkeypatch, nav_holder):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra)
    trajectory = [1000.0, 1052.0, 1080.0, 1101.0]
    rcs = []
    for nav in trajectory:
        nav_holder["nav"] = nav
        rcs.append(ra.tick("sim", 0.10))

    st = ra.load_state("sim")
    assert st["profit_locked"] is True
    assert st["peak_since_trigger"] == pytest.approx(1101.0)  # ratcheted up across ticks
    ev = _events(ra)
    assert ev.index("PROFIT_LOCK_ARMED") < ev.index("PROFIT_LOCK")  # armed before banked
    bank = [r for r in _rows(ra) if r["event"] == "PROFIT_LOCK"][-1]
    assert bank["kind"] == "bank" and bank["source"] == "daily_tick"
    assert rcs == [0, 0, 0, 1]  # bank stops the loop (rc 1)

    # a further tick is a no-op (book banked) — no new REBALANCE
    n_reb = ev.count("REBALANCE")
    nav_holder["nav"] = 1200.0
    assert ra.tick("sim", 0.10) == 0
    assert _events(ra).count("REBALANCE") == n_reb


def test_lifecycle_arm_then_trail(ra, monkeypatch, nav_holder):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra)
    # arm 1060, peak 1080 (floor 1047.6), give back to 1040 < floor -> trail
    for nav in [1000.0, 1060.0, 1080.0, 1040.0]:
        nav_holder["nav"] = nav
        ra.tick("sim", 0.10)
    st = ra.load_state("sim")
    assert st["profit_locked"] is True
    last = [r for r in _rows(ra) if r["event"] == "PROFIT_LOCK"][-1]
    assert last["kind"] == "trail"


def test_lifecycle_arms_only_once(ra, monkeypatch, nav_holder):
    # crossing the trigger on several consecutive ticks emits exactly ONE arm event
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra)
    for nav in [1052.0, 1060.0, 1070.0]:
        nav_holder["nav"] = nav
        ra.tick("sim", 0.10)
    assert _events(ra).count("PROFIT_LOCK_ARMED") == 1


# ------------------------------ unlock → re-arm ---------------------------- #
def test_lifecycle_unlock_then_rearm(ra, monkeypatch, nav_holder):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra)
    # bank it
    for nav in [1052.0, 1101.0]:
        nav_holder["nav"] = nav
        ra.tick("sim", 0.10)
    assert ra.load_state("sim")["profit_locked"] is True

    # deliberately unlock (anchor survives), then a high tick re-arms
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--unlock-profit"])
    assert ra.main() == 0
    st = ra.load_state("sim")
    assert st["profit_locked"] is False and st["profit_lock_armed"] is False
    assert st["campaign_start_nav"] == 1000.0

    nav_holder["nav"] = 1058.0  # +5.8% over the surviving anchor
    ra.tick("sim", 0.10)
    assert ra.load_state("sim")["profit_lock_armed"] is True
    assert _events(ra).count("PROFIT_LOCK_ARMED") == 2  # armed again after unlock


# ------------------------------ locked book over a run --------------------- #
def test_lifecycle_locked_book_banks_floor_each_qualifying_tick(ra, monkeypatch, nav_holder):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra, locked=True, balances={"USDT": 1100.0})
    nav_holder["nav"] = 1100.0
    monkeypatch.setattr(ra, "_trade_floor_shortfall", lambda cum, now=None: 2)
    for _ in range(3):
        assert ra.tick("sim", 0.10) == 0  # locked → never rebalances
    assert _events(ra).count("REBALANCE") == 0
    assert _events(ra).count("FLOOR_NUDGE") == 3  # one nudge per qualifying tick
    assert ra.load_state("sim")["cumulative_swaps"] >= 6


# ------------------------------ tick arms, dd-watch banks ------------------ #
def test_lifecycle_tick_arms_then_dd_watch_banks(ra, monkeypatch, nav_holder):
    _wire(ra, monkeypatch)
    _enable(ra, monkeypatch)
    _seed(ra)
    nav_holder["nav"] = 1060.0
    ra.tick("sim", 0.10)  # arms + sets HWM
    assert ra.load_state("sim")["profit_lock_armed"] is True

    nav_holder["nav"] = 1101.0  # intraday spike past bank
    assert ra.dd_watch("sim", 0.10) == 1
    st = ra.load_state("sim")
    assert st["profit_locked"] is True
    bank = [r for r in _rows(ra) if r["event"] == "PROFIT_LOCK"][-1]
    assert bank["source"] == "dd_watch" and bank["kind"] == "bank"
