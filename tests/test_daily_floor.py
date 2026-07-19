"""Contest >=1-trade/DAY floor: journal counting + the --ensure-daily-floor path.

Mirrors the importlib + tmp-redirect pattern of test_run_allocator_hardening.py.
All sim-only — no network, no real swaps.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_allocator.py"

IN_WINDOW_LATE = datetime(2026, 6, 24, 23, 0, tzinfo=timezone.utc)  # after 22:00 deadline
IN_WINDOW_EARLY = datetime(2026, 6, 24, 9, 0, tzinfo=timezone.utc)  # before the deadline
PRE_WINDOW = datetime(2026, 6, 15, 23, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("run_allocator_daily_floor", SCRIPT_PATH)
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
def floor_on(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "trade_floor_daily", True, raising=False)
    monkeypatch.setattr(ra.settings, "trade_floor_daily_deadline_utc", 22, raising=False)
    monkeypatch.setattr(ra.settings, "contest_start", "2026-06-22")
    monkeypatch.setattr(ra.settings, "contest_end", "2026-06-28")
    # Pin the min-swap floor so the "can't fund a sliver" case is deterministic regardless of the
    # local .env (live arming sets ALLOC_MIN_SWAP_USD=0.5, making a $0.5 seed *exactly* fundable →
    # the nudge banks instead of failing). A fixed 1.0 floor keeps $0.5 unfundable as the test intends.
    monkeypatch.setattr(ra.settings, "alloc_min_swap_usd", 1.0, raising=False)
    return ra


def _wire_prices(ra, monkeypatch):
    monkeypatch.setattr(ra, "price_fn", lambda *a, **k: lambda tok: 1.0 if tok == "USDT" else 100.0)


def _journal_write(ra, rows):
    with ra.SIM_JOURNAL.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _seed(ra, *, balances, halted=False, locked=False):
    st = {
        "hwm": None,
        "halted": halted,
        "balances": balances,
        "cumulative_swaps": 0,
        "window_start_ts": None,
    }
    if locked:
        st["profit_locked"] = True
    ra.save_state(st, "sim")


def _rows(tmp_path):
    jf = tmp_path / "allocator_journal.jsonl"
    if not jf.exists():
        return []
    return [json.loads(l) for l in jf.read_text().splitlines() if l.strip()]


# ------------------------------ _swaps_today -------------------------------- #
def test_swaps_today_counts_rebalance_and_nudges(ra):
    now = IN_WINDOW_LATE
    _journal_write(
        ra,
        [
            {"ts": "2026-06-23T10:00:00+00:00", "event": "REBALANCE", "n_swaps": 5},  # yesterday
            {"ts": "2026-06-24T04:00:00+00:00", "event": "REBALANCE", "n_swaps": 2},
            {"ts": "2026-06-24T12:00:00+00:00", "event": "FLOOR_NUDGE", "banked": 2},
            {"ts": "2026-06-24T16:00:00+00:00", "event": "DD_HALT"},  # not a swap
            {"ts": "2026-06-24T18:00:00+00:00", "event": "REBALANCE", "n_swaps": 0},
        ],
    )
    assert ra._swaps_today("sim", now) == 4


def test_swaps_today_empty_or_missing_journal(ra):
    assert ra._swaps_today("sim", IN_WINDOW_LATE) == 0
    _journal_write(ra, [{"ts": "garbage"}])
    ra.SIM_JOURNAL.write_text("not json\n")
    assert ra._swaps_today("sim", IN_WINDOW_LATE) == 0


# ------------------------------ gating -------------------------------------- #
def test_daily_floor_disabled_noop(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "trade_floor_daily", False, raising=False)
    # would explode if it tried to build a broker
    monkeypatch.setattr(ra, "build_broker", lambda *a, **k: 1 / 0)
    assert ra._daily_floor("sim", now=IN_WINDOW_LATE) == 0


def test_daily_floor_outside_window_noop(floor_on, monkeypatch):
    ra = floor_on
    monkeypatch.setattr(ra, "build_broker", lambda *a, **k: 1 / 0)
    assert ra._daily_floor("sim", now=PRE_WINDOW) == 0


def test_daily_floor_before_deadline_noop(floor_on, monkeypatch):
    ra = floor_on
    monkeypatch.setattr(ra, "build_broker", lambda *a, **k: 1 / 0)
    assert ra._daily_floor("sim", now=IN_WINDOW_EARLY) == 0


def test_daily_floor_respects_halt(floor_on, monkeypatch):
    ra = floor_on
    _seed(ra, balances={"USDT": 1000.0}, halted=True)
    monkeypatch.setattr(ra, "build_broker", lambda *a, **k: 1 / 0)
    assert ra._daily_floor("sim", now=IN_WINDOW_LATE) == 0


def test_daily_floor_noop_when_swap_already_today(floor_on, tmp_path, monkeypatch):
    ra = floor_on
    _seed(ra, balances={"USDT": 1000.0})
    _journal_write(ra, [{"ts": "2026-06-24T05:00:00+00:00", "event": "REBALANCE", "n_swaps": 1}])
    monkeypatch.setattr(ra, "build_broker", lambda *a, **k: 1 / 0)
    assert ra._daily_floor("sim", now=IN_WINDOW_LATE) == 0
    assert not any(r["event"] == "FLOOR_NUDGE" for r in _rows(tmp_path))


# ------------------------------ the nudge ----------------------------------- #
def test_daily_floor_banks_one_roundtrip_when_zero_today(floor_on, tmp_path, monkeypatch):
    ra = floor_on
    _wire_prices(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1000.0})
    assert ra._daily_floor("sim", now=IN_WINDOW_LATE) == 1
    nudge = [r for r in _rows(tmp_path) if r["event"] == "FLOOR_NUDGE"][-1]
    assert nudge["daily"] is True and nudge["banked"] == 2  # buy + sell-back legs
    st = ra.load_state("sim")
    assert st["cumulative_swaps"] == 2
    # round-trip keeps the book ~flat (tiny sliver fees only)
    assert st["balances"]["USDT"] == pytest.approx(1000.0, rel=0.01)


def test_daily_floor_works_while_profit_locked(floor_on, tmp_path, monkeypatch):
    # A banked campaign must still clear the >=1/day floor — the book is USDT-rich
    # and the nudge is ~0-impact, so no exposure is re-opened.
    ra = floor_on
    _wire_prices(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1100.0}, locked=True)
    assert ra._daily_floor("sim", now=IN_WINDOW_LATE) == 1
    assert ra.load_state("sim")["profit_locked"] is True  # lock untouched


def test_daily_floor_failed_nudge_journaled(floor_on, tmp_path, monkeypatch):
    ra = floor_on
    _wire_prices(ra, monkeypatch)
    _seed(ra, balances={"USDT": 0.5})  # can't fund a sliver
    assert ra._daily_floor("sim", now=IN_WINDOW_LATE) == 2
    failed = [r for r in _rows(tmp_path) if r["event"] == "FLOOR_NUDGE_FAILED"]
    assert failed and failed[-1]["daily"] is True and failed[-1]["need"] == 1


def test_daily_floor_lock_wrapper_dispatch(floor_on, monkeypatch):
    # the CLI path goes through the per-mode lock wrapper
    ra = floor_on
    _wire_prices(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1000.0})
    monkeypatch.setattr(ra, "_daily_floor", lambda mode, now=None: 42)
    assert ra.daily_floor("sim") == 42


# --------------------------- _swaps_today None-handling -------------------- #
def test_swaps_today_treats_missing_or_none_counts_as_zero(ra):
    _journal_write(
        ra,
        [
            {"ts": "2026-06-24T04:00:00+00:00", "event": "REBALANCE", "n_swaps": None},
            {"ts": "2026-06-24T05:00:00+00:00", "event": "REBALANCE"},  # no n_swaps key
            {"ts": "2026-06-24T06:00:00+00:00", "event": "FLOOR_NUDGE", "banked": None},
            {"ts": "2026-06-24T07:00:00+00:00", "event": "REBALANCE", "n_swaps": 3},
        ],
    )
    assert ra._swaps_today("sim", IN_WINDOW_LATE) == 3


# --------------------------- deadline-hour boundary ------------------------ #
def test_daily_floor_fires_exactly_at_deadline_hour(floor_on, tmp_path, monkeypatch):
    ra = floor_on
    _wire_prices(ra, monkeypatch)
    _seed(ra, balances={"USDT": 1000.0})
    at_deadline = datetime(2026, 6, 24, 22, 0, tzinfo=timezone.utc)  # hour == 22, not < 22
    assert ra._daily_floor("sim", now=at_deadline) == 1
    assert any(r["event"] == "FLOOR_NUDGE" for r in _rows(tmp_path))


# --------------------------- _trade_floor_shortfall window/lookahead edges - #
def test_shortfall_zero_when_floor_already_met(floor_on):
    ra = floor_on
    assert ra._trade_floor_shortfall(7, now=datetime(2026, 6, 28, tzinfo=timezone.utc)) == 0


def test_shortfall_zero_at_window_start_too_early(floor_on):
    # at contest_start the deadline is ~6 days off (> lookahead 2) -> no nudge yet
    ra = floor_on
    start = datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc)
    assert ra._trade_floor_shortfall(0, now=start) == 0


def test_shortfall_fires_at_window_end(floor_on):
    ra = floor_on
    end = datetime(2026, 6, 28, 0, 0, tzinfo=timezone.utc)
    assert ra._trade_floor_shortfall(3, now=end) == 4  # 7 - 3


def test_shortfall_lookahead_is_inclusive(floor_on):
    # days_left == lookahead (2.0) fires; just over (2.04) does not (`> lookahead` skips)
    ra = floor_on
    exactly_2d = datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc)  # end - 2d
    just_over = datetime(2026, 6, 25, 23, 0, tzinfo=timezone.utc)  # end - 2.04d
    assert ra._trade_floor_shortfall(5, now=exactly_2d) == 2
    assert ra._trade_floor_shortfall(5, now=just_over) == 0
