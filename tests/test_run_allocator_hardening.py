"""
Hardening tests for scripts/run_allocator.py (the live spot-agent runtime).

Covers the operational-safety refinements: atomic state writes + corrupt-file
recovery (Phase 0). Grown across later phases (idempotency lock, trade-floor, …).

The script lives in scripts/ (not the package), so we load it via importlib —
same pattern as test_edge_check.py / test_session_report.py.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_allocator.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_allocator_hardening", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ra(tmp_path, monkeypatch):
    """run_allocator module with state paths redirected into a tmp dir."""
    mod = _load()
    monkeypatch.setattr(mod, "SIM_STATE", tmp_path / "allocator_state.json")
    monkeypatch.setattr(mod, "LIVE_STATE", tmp_path / "allocator_live_state.json")
    return mod


# --------------------------- Phase 0: atomic state ------------------------- #
def test_save_state_round_trips(ra):
    state = {"hwm": 1234.5, "halted": False, "balances": {"USDT": 100.0}}
    ra.save_state(state, "sim")
    assert ra.load_state("sim") == state


def test_save_state_leaves_no_tmp_file(ra, tmp_path):
    ra.save_state({"hwm": 1.0, "halted": False, "balances": None}, "live")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"atomic write left a tmp file: {leftovers}"
    # and the real file is valid JSON
    data = json.loads((tmp_path / "allocator_live_state.json").read_text())
    assert data["hwm"] == 1.0


def test_save_state_is_atomic_overwrite(ra):
    ra.save_state({"hwm": 100.0, "halted": False, "balances": None}, "sim")
    ra.save_state({"hwm": 200.0, "halted": True, "balances": None}, "sim")
    out = ra.load_state("sim")
    assert out["hwm"] == 200.0 and out["halted"] is True


def test_load_state_recovers_from_corrupt_file(ra):
    # A crash mid-write could leave truncated JSON; load_state must NOT propagate
    # garbage as a real HWM — it returns the safe default (hwm=None).
    ra.state_path("sim").write_text('{"hwm": 995')  # truncated / invalid JSON
    out = ra.load_state("sim")
    assert out["hwm"] is None
    assert out["halted"] is False


def test_load_state_default_when_absent(ra):
    out = ra.load_state("live")
    assert out["hwm"] is None and out["halted"] is False and out["balances"] is None
    assert out["cumulative_swaps"] == 0 and out["window_start_ts"] is None


# --------------------------- Phase 2: risk guards -------------------------- #
def test_resume_clears_halt(ra, monkeypatch):
    ra.save_state({"hwm": 100.0, "halted": True, "balances": None}, "sim")
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--resume"])
    assert ra.main() == 0
    assert ra.load_state("sim")["halted"] is False


def test_resume_noop_when_not_halted(ra, monkeypatch):
    ra.save_state({"hwm": 100.0, "halted": False, "balances": None}, "live")
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "live", "--resume"])
    assert ra.main() == 0
    assert ra.load_state("live")["halted"] is False


def test_resume_blocks_on_partial_flatten_without_force(ra, tmp_path, monkeypatch):
    # a halt whose emergency-flatten left a failed leg → --resume must refuse without --force,
    # so an operator can't silently trade on top of possible residual exposure.
    monkeypatch.setattr(ra, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(ra, "SIM_JOURNAL", tmp_path / "allocator_journal.jsonl")
    ra.journal(
        {
            "ts": "2026-06-14T00:00:00+00:00",
            "event": "DD_HALT",
            "mode": "sim",
            "flattened_ok": 1,
            "flattened_attempted": 2,
            "flatten_partial": True,
            "flatten_errors": ["rpc 503"],
        },
        "sim",
    )
    ra.save_state({"hwm": 100.0, "halted": True, "balances": None}, "sim")
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--resume"])
    assert ra.main() == 2  # BLOCKED: partial flatten, no --force
    assert ra.load_state("sim")["halted"] is True
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "sim", "--resume", "--force"])
    assert ra.main() == 0  # --force acknowledges the residual
    assert ra.load_state("sim")["halted"] is False


def test_bar_age_hours_detects_recent_vs_stale(ra):
    now = datetime.now(timezone.utc)
    recent = pd.DataFrame(
        {"close": [1.0, 2.0]},
        index=pd.to_datetime([now - timedelta(hours=2), now - timedelta(hours=1)]),
    )
    assert ra._bar_age_hours(recent) < 3.0
    stale = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime([now - timedelta(hours=48)]))
    assert ra._bar_age_hours(stale) > 24.0


def test_bar_age_hours_tz_naive_index(ra):
    # tz-naive timestamps are treated as UTC (no crash)
    naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    df = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime([naive]))
    age = ra._bar_age_hours(df)
    assert age is not None and 0 <= age < 3.0


# --------------------------- Phase 3: idempotency -------------------------- #
def test_lock_prevents_concurrent_tick(ra, tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "JOURNAL_DIR", tmp_path)
    fd1 = ra._acquire_lock("live")
    assert fd1 is not None
    try:
        assert ra._acquire_lock("live") is None  # second acquire is blocked
    finally:
        fcntl.flock(fd1, fcntl.LOCK_UN)
        os.close(fd1)
    fd3 = ra._acquire_lock("live")  # released -> re-acquirable
    assert fd3 is not None
    fcntl.flock(fd3, fcntl.LOCK_UN)
    os.close(fd3)


def test_lock_is_per_mode(ra, tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "JOURNAL_DIR", tmp_path)
    fd_live = ra._acquire_lock("live")
    fd_sim = ra._acquire_lock("sim")  # different mode -> not blocked
    assert fd_live is not None and fd_sim is not None
    for fd in (fd_live, fd_sim):
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --------------------------- Phase 5: preflight + recon -------------------- #
class _FakeClient:
    def __init__(self, bals):
        self._b = bals

    def balances(self):
        return self._b


def _live_creds(ra, monkeypatch, *, access="a", hmac="b", wallet="pw", enabled=True):
    monkeypatch.setattr(ra.settings, "twak_access_id", access)
    monkeypatch.setattr(ra.settings, "twak_hmac_secret", hmac)
    monkeypatch.setattr(ra.settings, "twak_wallet_password", wallet)
    monkeypatch.setattr(ra.settings, "agent_wallet_password", "")
    monkeypatch.setattr(ra.settings, "enable_live_trading", enabled)


def test_preflight_ok_with_full_creds(ra, monkeypatch):
    _live_creds(ra, monkeypatch)
    assert ra._live_preflight() is None


def test_preflight_fails_missing_api_creds(ra, monkeypatch):
    _live_creds(ra, monkeypatch, access="", hmac="")
    assert ra._live_preflight() == 2


def test_preflight_fails_missing_wallet_pw(ra, monkeypatch):
    _live_creds(ra, monkeypatch, wallet="")
    assert ra._live_preflight() == 2


def test_preflight_fails_when_live_trading_disabled(ra, monkeypatch):
    _live_creds(ra, monkeypatch, enabled=False)
    assert ra._live_preflight() == 2


def test_preflight_fails_when_kill_switch_engaged(ra, monkeypatch):
    # the kill switch outranks creds — an engaged sentinel halts EVERY live entry point
    # (tick/dd_watch/daily_floor all go through _live_preflight), including a running --loop.
    _live_creds(ra, monkeypatch)  # otherwise-valid live setup
    monkeypatch.setattr(ra.kill_switch, "is_engaged", lambda: True)
    assert ra._live_preflight() == 2
    monkeypatch.setattr(ra.kill_switch, "is_engaged", lambda: False)
    assert ra._live_preflight() is None  # released -> proceeds


def test_live_tick_halts_on_kill_switch(ra, tmp_path, monkeypatch):
    # a FULL live tick with the kill switch engaged must skip (rc=2) BEFORE any broker is built.
    monkeypatch.setattr(ra, "JOURNAL_DIR", tmp_path)
    _live_creds(ra, monkeypatch)
    monkeypatch.setattr(ra.kill_switch, "is_engaged", lambda: True)
    monkeypatch.setattr(
        ra,
        "build_broker",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not build broker")),
    )
    assert ra.tick("live", 0.30) == 2


# ------------------- live-promotion safety (a non-default arm goes live) ------------------- #
def test_live_resolves_promoted_challenger(ra, monkeypatch):
    # STRATEGY_NAME promotes a non-default arm; LIVE resolves to it (not the locked default).
    monkeypatch.setattr(ra.settings, "strategy_name", "dual_momentum")
    monkeypatch.setattr(ra.settings, "alloc_adaptive", True)
    assert ra._resolve_strategy_name("live") == "dual_momentum"


def test_live_ignores_sim_selector_uses_env(ra, monkeypatch):
    # Contest-safety: a dashboard "click" (the SIM selector) can move SIM but NEVER LIVE.
    monkeypatch.setattr(ra.settings, "strategy_name", "dual_momentum")
    monkeypatch.setattr(ra._strategy_select, "load", lambda default: "rotation")
    assert ra._resolve_strategy_name("sim") == "rotation"  # SIM honors the selector…
    assert ra._resolve_strategy_name("live") == "dual_momentum"  # …LIVE uses the env lock


def test_promoted_challenger_runs_through_tick_to_broker(ra, tmp_path, monkeypatch):
    # End-to-end: a promoted challenger resolves → registry.get → target_weights_now → the
    # STRATEGY-AGNOSTIC broker.rebalance → journal. The dispatch code path is identical for sim
    # and live (only the broker/client + resolve differ), so a SIM tick with STRATEGY_NAME set
    # proves a non-default arm runs the full pipeline safely.
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    monkeypatch.setattr(ra.settings, "strategy_name", "dual_momentum")
    monkeypatch.setattr(ra.settings, "alloc_adaptive", True)
    monkeypatch.setattr(
        ra._strategy_select, "load", lambda default: default
    )  # isolate from real selector
    assert ra.tick("sim", 0.30) == 0
    reb = [r for r in _journal_rows(tmp_path) if r["event"] == "REBALANCE"][-1]
    assert reb["strategy"] == "dual_momentum"  # the promoted challenger ran the tick


def test_preflight_only_ok_arms_without_trading(ra, monkeypatch):
    _live_creds(ra, monkeypatch)  # full creds + ENABLE_LIVE_TRADING
    monkeypatch.setattr(ra.settings, "strategy_name", "dual_momentum")
    monkeypatch.setattr(
        ra, "tick", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not tick"))
    )
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "live", "--preflight-only"])
    assert ra.main() == 0  # validates + exits, never trades


def test_preflight_only_fails_when_live_disabled(ra, monkeypatch):
    _live_creds(ra, monkeypatch, enabled=False)  # ENABLE_LIVE_TRADING off
    monkeypatch.setattr(
        ra, "tick", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not tick"))
    )
    monkeypatch.setattr(sys, "argv", ["run_allocator.py", "--mode", "live", "--preflight-only"])
    assert ra.main() == 2


def test_reconcile_none_when_no_expected(ra):
    assert ra._reconcile_live(_FakeClient({"USDT": 100.0}), None) is None
    assert ra._reconcile_live(_FakeClient({"USDT": 100.0}), {}) is None


def test_reconcile_detects_drift(ra):
    c = _FakeClient({"USDT": 80.0, "BNB": 0.10})
    drift = ra._reconcile_live(c, {"USDT": 100.0, "BNB": 0.10}, tol=0.02)
    assert "USDT" in drift and "BNB" not in drift


def test_reconcile_within_tolerance(ra):
    assert ra._reconcile_live(_FakeClient({"USDT": 100.5}), {"USDT": 100.0}, tol=0.02) is None


# --------------------------- Phase 7: full sim tick ------------------------ #
def _wire_sim_tick(ra, tmp_path, monkeypatch, *, fg=55):
    """Redirect paths to tmp + mock the CMC/price feeds so a full sim tick runs
    offline and deterministically."""
    monkeypatch.setattr(ra, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(ra, "SIM_JOURNAL", tmp_path / "allocator_journal.jsonl")
    monkeypatch.setattr(ra, "SIM_STATE", tmp_path / "allocator_state.json")

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
    # Pin x402 OFF so the default-off sim tick is deterministic regardless of the dev
    # machine's .env (which now sets X402_ENABLED=true for the live agent). The enabled
    # branch is exercised separately in test_full_sim_tick_x402_failure_flagged.
    monkeypatch.setattr(ra.settings, "x402_enabled", False, raising=False)


def _journal_rows(tmp_path):
    jf = tmp_path / "allocator_journal.jsonl"
    return [json.loads(line) for line in jf.read_text().splitlines() if line.strip()]


def test_full_sim_tick_journals_enriched_rebalance(ra, tmp_path, monkeypatch):
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    assert ra.tick("sim", 0.30) == 0
    reb = [r for r in _journal_rows(tmp_path) if r["event"] == "REBALANCE"][-1]
    for key in (
        "n_swaps",
        "n_swaps_total",
        "n_failed",
        "failed_swaps",
        "cumulative_swaps",
        "fear_greed_available",
        "rationale",
        "x402_attempted",
        "x402_failed",
    ):
        assert key in reb, f"missing {key} in journal row"
    assert reb["fear_greed_available"] is True
    assert reb["cumulative_swaps"] == reb["n_swaps"]
    # C1: x402 off by default -> attempted/failed both False (disambiguated from a real miss)
    assert reb["x402_attempted"] is False and reb["x402_failed"] is False
    # the SIM journal got written; the LIVE one did not (no contamination)
    assert not (tmp_path / "allocator_live.jsonl").exists()


def test_full_sim_tick_dd_halt(ra, tmp_path, monkeypatch):
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    # a high prior HWM forces the drawdown halt on the first tick
    ra.save_state(
        {
            "hwm": 10_000.0,
            "halted": False,
            "balances": None,
            "cumulative_swaps": 0,
            "window_start_ts": None,
        },
        "sim",
    )
    assert ra.tick("sim", 0.30) == 1
    last = _journal_rows(tmp_path)[-1]
    assert last["event"] == "DD_HALT"
    assert last["dd"] > 0.30
    assert ra.load_state("sim")["halted"] is True
    # partial-flatten signal: a clean SIM flatten journals ok == attempted, not partial
    assert last["flattened_ok"] == last["flattened_attempted"] and last["flatten_partial"] is False


def test_flatten_fields_distinguishes_confirmed_sells_from_attempts(ra):
    # the risk-rail fix: a leg that fails after retries must NOT be counted as flattened.
    from ictbot.exec.twak_client import SwapResult

    ok = SwapResult("BNB", "USDT", 1.0, 600.0, 600.0, 0.5, tx="0xok", ok=True)
    bad = SwapResult("ETH", "USDT", 1.0, 0.0, 0.0, 0.0, tx="", ok=False, error="rpc 503")
    partial = ra._flatten_fields([ok, bad])
    assert partial["flattened_attempted"] == 2 and partial["flattened_ok"] == 1
    assert partial["flatten_partial"] is True and partial["flatten_errors"] == ["rpc 503"]
    assert partial["flattened"] == 2  # back-compat key kept
    clean = ra._flatten_fields([ok])
    assert clean["flattened_ok"] == 1 and clean["flatten_partial"] is False


def test_full_sim_tick_skips_on_zero_price(ra, tmp_path, monkeypatch):
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    # a bad price feed must SKIP (rc=2), never trade or false-halt
    monkeypatch.setattr(ra, "price_fn", lambda *a, **k: lambda tok: 0.0)
    assert ra.tick("sim", 0.30) == 2
    assert not (tmp_path / "allocator_journal.jsonl").exists()


def test_full_sim_tick_skips_on_price_read_raise(ra, tmp_path, monkeypatch):
    # A3: a price read that RAISES (cmc.price -> RuntimeError on CMC+Binance double-miss)
    # must SKIP cleanly (rc=2), not abort the tick with a traceback before the guard.
    _wire_sim_tick(ra, tmp_path, monkeypatch)

    def _raise(tok):
        raise RuntimeError(f"no price available for {tok} (CMC + Binance both failed)")

    monkeypatch.setattr(ra, "price_fn", lambda *a, **k: _raise)
    assert ra.tick("sim", 0.30) == 2
    assert not (tmp_path / "allocator_journal.jsonl").exists()


def test_live_tick_preflight_missing_wallet_pw(ra, tmp_path, monkeypatch):
    # D1: a FULL tick("live", ...) must return 2 (not 0) when the wallet password is
    # absent — so rc-based monitoring catches a refactor that bypassed the preflight.
    monkeypatch.setattr(ra, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(ra.settings, "twak_access_id", "aid")
    monkeypatch.setattr(ra.settings, "twak_hmac_secret", "hs")
    monkeypatch.setattr(ra.settings, "twak_wallet_password", "")
    monkeypatch.setattr(ra.settings, "agent_wallet_password", "")
    monkeypatch.setattr(ra.settings, "enable_live_trading", True)
    assert ra.tick("live", 0.30) == 2


def test_full_sim_tick_floor_nudge_failed_journaled(ra, tmp_path, monkeypatch):
    # D2: when the trade-floor nudge can't bank (banked==0), the tick journals a
    # FLOOR_NUDGE_FAILED event (not just a stdout WARNING) and still completes.
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    monkeypatch.setattr(ra, "_trade_floor_shortfall", lambda cum, now=None: 3)
    monkeypatch.setattr(ra, "_ensure_trade_floor", lambda broker, prices, need, **kw: ([], 0))
    assert ra.tick("sim", 0.30) == 0
    failed = [r for r in _journal_rows(tmp_path) if r["event"] == "FLOOR_NUDGE_FAILED"]
    assert len(failed) == 1
    assert failed[0]["need"] == 3


def test_full_sim_tick_x402_failure_flagged(ra, tmp_path, monkeypatch):
    # C1: x402 enabled but the read raises -> journal marks attempted=True, failed=True
    # (distinct from disabled, where both are False).
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    monkeypatch.setattr(ra.settings, "x402_enabled", True, raising=False)

    def _boom(*a, **k):
        raise RuntimeError("x402 pay wallet exhausted")

    monkeypatch.setattr("ictbot.data.x402_cmc.dex_search", _boom, raising=False)
    assert ra.tick("sim", 0.30) == 0
    reb = [r for r in _journal_rows(tmp_path) if r["event"] == "REBALANCE"][-1]
    assert reb["x402_attempted"] is True and reb["x402_failed"] is True
    assert reb["x402_dex"] is None


# --------------------- Phase G: fast flatten-only DD monitor --------------- #
def _seed_state(ra, *, hwm, halted=False, balances=None):
    ra.save_state(
        {
            "hwm": hwm,
            "halted": halted,
            "balances": balances if balances is not None else {"USDT": 50.0, "BNB": 1.0},
            "cumulative_swaps": 0,
            "window_start_ts": None,
        },
        "sim",
    )


def test_dd_watch_flattens_and_halts_on_breach(ra, tmp_path, monkeypatch):
    # G: a breach of the persisted HWM flattens + halts and journals a dd_watch DD_HALT.
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    _seed_state(ra, hwm=10_000.0)  # NAV ~150 vs HWM 10k -> dd >> cap
    assert ra.dd_watch("sim", 0.30) == 1
    st = ra.load_state("sim")
    assert st["halted"] is True
    last = _journal_rows(tmp_path)[-1]
    assert last["event"] == "DD_HALT" and last["source"] == "dd_watch"
    assert last["flattened"] >= 1
    assert st["balances"].get("BNB", 0.0) == pytest.approx(0.0, abs=1e-9)  # book flat


def test_dd_watch_noop_within_cap(ra, tmp_path, monkeypatch):
    # G: NAV at/above HWM -> no action, no halt, no journal (never trades).
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    _seed_state(ra, hwm=150.0)  # NAV ~150 ~ HWM -> dd ~ 0
    assert ra.dd_watch("sim", 0.30) == 0
    assert ra.load_state("sim")["halted"] is False
    rows = _journal_rows(tmp_path) if (tmp_path / "allocator_journal.jsonl").exists() else []
    assert all(r["event"] != "DD_HALT" for r in rows)


def test_dd_watch_skips_on_bad_price_no_false_flatten(ra, tmp_path, monkeypatch):
    # G: a bad price must SKIP (rc=2) and NEVER false-flatten/halt on bad data.
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    _seed_state(ra, hwm=10_000.0)
    monkeypatch.setattr(ra, "price_fn", lambda *a, **k: lambda tok: 0.0)
    assert ra.dd_watch("sim", 0.30) == 2
    assert ra.load_state("sim")["halted"] is False


def test_dd_watch_noop_when_already_halted(ra, tmp_path, monkeypatch):
    # G: once halted, the monitor is a no-op (the book is already flat).
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    _seed_state(ra, hwm=10_000.0, halted=True)
    assert ra.dd_watch("sim", 0.30) == 0


def test_dd_watch_noop_without_persisted_hwm(ra, tmp_path, monkeypatch):
    # G: no baseline HWM yet -> no-op; the monitor must NOT seed one or flatten.
    _wire_sim_tick(ra, tmp_path, monkeypatch)
    _seed_state(ra, hwm=None)
    assert ra.dd_watch("sim", 0.30) == 0
    assert ra.load_state("sim")["halted"] is False


# ----------------- UI token toggles: broker trading universe --------------- #
def test_build_broker_universe_is_active_union_held(ra):
    """A deselected token with a balance must STAY in the broker loop so the
    next rebalance sells it (target 0) instead of stranding it."""
    state = {"balances": {"USDT": 500.0, "CAKE": 100.0}}
    broker, _ = ra.build_broker("sim", lambda t: 1.0, state, active=["BNB", "ETH"])
    assert list(broker.tokens) == ["BNB", "ETH", "CAKE"]  # canonical order


def test_build_broker_universe_unknown_holdings_degrades_to_full(ra):
    """No persisted balances (first live run) -> full universe, identical to legacy."""
    broker, _ = ra.build_broker("sim", lambda t: 1.0, {}, active=["BNB", "ETH"])
    assert tuple(broker.tokens) == ra.CONTEST_TOKENS


def test_build_broker_no_active_is_legacy(ra):
    broker, _ = ra.build_broker("sim", lambda t: 1.0, {"balances": {"USDT": 1.0}})
    assert tuple(broker.tokens) == ra.CONTEST_TOKENS


def test_build_broker_dust_and_zero_not_held(ra):
    """qty 0 / None entries are not 'held' — only genuinely positive balances pin
    a deselected token into the trading universe."""
    state = {"balances": {"USDT": 500.0, "CAKE": 0.0, "DOGE": None}}
    broker, _ = ra.build_broker("sim", lambda t: 1.0, state, active=["BNB", "ETH"])
    assert list(broker.tokens) == ["BNB", "ETH"]


def test_build_broker_live_held_from_onchain_not_snapshot(ra, monkeypatch):
    """LIVE: `held` must come from client.balances() (on-chain truth), not the
    previous tick's journal snapshot — a stale snapshot must never strand an
    on-chain position outside broker.tokens (false-DD-halt vector)."""

    class StubClient:
        def balances(self):
            return {"USDT": 10.0, "DOGE": 50.0}  # on-chain truth: DOGE held

    monkeypatch.setattr(ra, "make_client", lambda *a, **k: StubClient())
    monkeypatch.setattr(ra.settings, "enable_live_trading", True)  # broker guard
    # snapshot (stale) says nothing about DOGE
    state = {"balances": {"USDT": 500.0}}
    broker, _ = ra.build_broker("live", lambda t: 1.0, state, active=["BNB", "ETH"])
    assert "DOGE" in broker.tokens  # on-chain held wins
    assert list(broker.tokens) == ["BNB", "ETH", "DOGE"]


def test_build_broker_live_balance_read_failure_degrades_to_full(ra, monkeypatch):
    """LIVE: if the on-chain read fails we can't know what's held — degrade to
    the full universe (legacy behavior), never to a restricted set."""

    class StubClient:
        def balances(self):
            raise RuntimeError("rpc down")

    monkeypatch.setattr(ra, "make_client", lambda *a, **k: StubClient())
    monkeypatch.setattr(ra.settings, "enable_live_trading", True)  # broker guard
    broker, _ = ra.build_broker(
        "live", lambda t: 1.0, {"balances": {"USDT": 500.0}}, active=["BNB", "ETH"]
    )
    assert tuple(broker.tokens) == ra.CONTEST_TOKENS
