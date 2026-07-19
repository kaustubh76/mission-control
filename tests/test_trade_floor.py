"""Contest trade-floor auto-ensure: window gating + bounded round-trip nudges."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ictbot.exec.bsc_spot_live import TwakSpotBroker
from ictbot.exec.twak_client import SimTwakClient

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_allocator.py"
PRICES = {"BNB": 600.0, "ETH": 3000.0, "CAKE": 2.0}


def _load():
    spec = importlib.util.spec_from_file_location("run_allocator_floor", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ra():
    return _load()


@pytest.fixture
def window(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "contest_start", "2026-06-22")
    monkeypatch.setattr(ra.settings, "contest_end", "2026-06-28")
    monkeypatch.setattr(ra.settings, "trade_floor_min", 7)
    monkeypatch.setattr(ra.settings, "trade_floor_lookahead_days", 2.0)
    return ra


def _broker(start_usdt=1000.0):
    client = SimTwakClient(lambda t: PRICES[t], start_usdt=start_usdt)
    return TwakSpotBroker(client, tokens=list(PRICES), min_swap_usd=1.0)


# --------------------------- shortfall gating ------------------------------ #
def test_no_shortfall_when_floor_met(window):
    assert window._trade_floor_shortfall(7) == 0
    assert window._trade_floor_shortfall(12) == 0


def test_no_shortfall_before_window(window):
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)  # pre-contest
    assert window._trade_floor_shortfall(0, now=now) == 0


def test_no_shortfall_early_in_window(window):
    now = datetime(2026, 6, 23, tzinfo=timezone.utc)  # ~5 days left > 2 lookahead
    assert window._trade_floor_shortfall(0, now=now) == 0


def test_shortfall_fires_near_deadline(window):
    now = datetime(2026, 6, 27, tzinfo=timezone.utc)  # ~1 day left <= lookahead
    assert window._trade_floor_shortfall(3, now=now) == 4
    assert window._trade_floor_shortfall(7, now=now) == 0


def test_shortfall_safe_on_bad_dates(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "contest_end", "not-a-date")
    assert ra._trade_floor_shortfall(0, now=datetime(2026, 6, 27, tzinfo=timezone.utc)) == 0


# --------------------------- the nudge itself ------------------------------ #
def test_ensure_trade_floor_banks_swaps_flat_nav(ra):
    broker = _broker()
    nav0 = broker.nav(broker.prices())
    swaps, banked = ra._ensure_trade_floor(broker, broker.prices(), needed=4)
    assert banked >= 4
    assert all(s.ok for s in swaps)
    # round-trips keep NAV ~flat (only tiny sliver fees)
    assert broker.nav(broker.prices()) == pytest.approx(nav0, rel=0.01)


def test_ensure_trade_floor_stops_without_quote(ra):
    # no USDT to fund a sliver -> banks nothing, no crash
    client = SimTwakClient(lambda t: PRICES[t], start_usdt=0.0)
    broker = TwakSpotBroker(client, tokens=list(PRICES), min_swap_usd=1.0)
    swaps, banked = ra._ensure_trade_floor(broker, broker.prices(), needed=4)
    assert banked == 0


def test_ensure_trade_floor_stops_on_swap_failure(ra):
    # D2: a failing (live-like) swap banks nothing and returns cleanly — never raises.
    from ictbot.exec.twak_client import SwapResult

    class _FailingClient:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def swap(self, f, t, amt, *, execute=True):
            return SwapResult(f, t, amt, 0.0, 0.0, 0.0, tx="", ok=False, error="forced")

    inner = SimTwakClient(lambda t: PRICES[t], start_usdt=1000.0)
    broker = TwakSpotBroker(_FailingClient(inner), tokens=list(PRICES), min_swap_usd=1.0)
    swaps, banked = ra._ensure_trade_floor(broker, broker.prices(), needed=4)
    assert banked == 0
    assert swaps and all(not s.ok for s in swaps)  # the failed leg is captured


# --------------------------- M3: remaining branch coverage ------------------ #
def test_ensure_trade_floor_partial_bank_on_sell_leg_failure(ra):
    """Buy leg settles, the sell-back fails: the settled buy still counts (banked==1),
    the loop breaks cleanly at the `if not s2.ok: break` and never raises."""
    from ictbot.exec.twak_client import SwapResult

    class _SellFailClient:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def swap(self, f, t, amt, *, execute=True):
            if f == "USDT":  # buy leg: real sim fill
                return self._inner.swap(f, t, amt, execute=execute)
            return SwapResult(f, t, amt, 0.0, 0.0, 0.0, tx="", ok=False, error="forced-sell")

    inner = SimTwakClient(lambda t: PRICES[t], start_usdt=1000.0)
    broker = TwakSpotBroker(_SellFailClient(inner), tokens=list(PRICES), min_swap_usd=1.0)
    swaps, banked = ra._ensure_trade_floor(broker, broker.prices(), needed=4)
    assert banked == 1
    assert [s.ok for s in swaps] == [True, False]  # settled buy + the failed sell-back


def test_ensure_trade_floor_odd_needed_terminates(ra):
    """Round-trips bank 2 legs per iteration, so needed=3 terminates at banked==4
    (overshoot-by-one is the documented behaviour, not an infinite loop)."""
    broker = _broker()
    swaps, banked = ra._ensure_trade_floor(broker, broker.prices(), needed=3)
    assert banked == 4
    assert all(s.ok for s in swaps)


def test_ensure_trade_floor_uses_largest_holding_token(ra):
    """The nudge round-trips the token with the largest USD holding
    (max(holdings_usd)), not blindly tokens[0] (= BNB here). [legacy, pick=None]"""
    broker = _broker()
    assert broker.client.swap("USDT", "ETH", 300.0).ok  # seed an ETH position (now the largest)
    swaps, banked = ra._ensure_trade_floor(broker, broker.prices(), needed=2)
    assert banked >= 2
    legs = {(s.from_token, s.to_token) for s in swaps if s.ok}
    assert legs == {("USDT", "ETH"), ("ETH", "USDT")}  # ETH, not tokens[0]=BNB


# --------------------------- token ROTATION (touch all 8) ------------------- #
def test_floor_picker_round_robins_and_wraps(ra):
    toks = ["A", "B", "C"]
    pick, get_cursor = ra._floor_picker(2, toks)
    assert [pick(), pick(), pick(), pick()] == ["C", "A", "B", "C"]  # wraps past the end
    assert get_cursor() == 6  # advanced by 4 picks


def test_ensure_trade_floor_rotates_one_token_per_round_trip(ra):
    """With a `pick`, each round-trip nudges the NEXT token (round-robin), not the largest holding."""
    broker = _broker()  # tokens BNB, ETH, CAKE
    pick, _ = ra._floor_picker(0, broker.tokens)
    swaps, banked = ra._ensure_trade_floor(broker, broker.prices(), needed=6, pick=pick)
    bought = [s.to_token for s in swaps if s.ok and s.from_token == "USDT"]
    assert bought == ["BNB", "ETH", "CAKE"]  # 3 round-trips, one distinct token each


def test_rotation_touches_every_token_over_the_week(ra):
    """8 successive daily-floor nudges (1 round-trip each), cursor persisting between, touch all 8
    universe tokens — the contest goal: never leave a token untouched."""
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    toks, seen, cursor = list(CONTEST_TOKENS), set(), 0
    for _ in range(len(toks)):  # one "day" each
        pick, get_cursor = ra._floor_picker(cursor, toks)
        seen.add(pick())  # the day's single nudge token
        cursor = get_cursor()  # persist for the next day
    assert seen == set(toks)  # all 8 touched


def test_floor_nudge_off_uses_legacy_largest_holding(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "trade_floor_rotate", False)
    broker = _broker()
    assert broker.client.swap("USDT", "ETH", 300.0).ok  # ETH = largest holding
    state = {"floor_cursor": 0}
    swaps, banked = ra._floor_nudge(broker, broker.prices(), 2, state)
    legs = {(s.from_token, s.to_token) for s in swaps if s.ok}
    assert legs == {("USDT", "ETH"), ("ETH", "USDT")}  # legacy path, cursor untouched
    assert state["floor_cursor"] == 0


def test_floor_nudge_on_rotates_and_persists_cursor(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "trade_floor_rotate", True)
    broker = _broker()
    state = {"floor_cursor": 0}
    ra._floor_nudge(broker, broker.prices(), 2, state)  # 1 round-trip -> 1 pick
    assert state["floor_cursor"] == 1  # advanced + persisted for the next tick


# --------------------------- _nudged_tokens (journal/UI) ------------------- #
def test_nudged_tokens_extracts_distinct_buy_legs(ra):
    """The journal/UI need WHICH tokens a floor nudge touched — the buy legs' to_token,
    distinct + in first-seen order (so the dashboard can show per-token rotation)."""
    broker = _broker()
    pick, _ = ra._floor_picker(1, broker.tokens)  # start at ETH -> CAKE -> BNB...
    swaps, banked = ra._ensure_trade_floor(broker, broker.prices(), needed=4, pick=pick)
    assert ra._nudged_tokens(swaps, broker.quote) == ["ETH", "CAKE"]  # 2 round-trips, 2 picks


def test_nudged_tokens_skips_failed_and_quote_legs(ra):
    from ictbot.exec.twak_client import SwapResult

    swaps = [
        SwapResult("USDT", "BNB", 2, 0.003, 600, 0.001, tx="a", ok=True),
        SwapResult(
            "BNB", "USDT", 0.003, 2, 600, 0.001, tx="b", ok=True
        ),  # sell leg: to=USDT excluded
        SwapResult("USDT", "ETH", 2, 0.0, 3000, 0.001, tx="c", ok=False),  # failed buy excluded
        SwapResult("USDT", "BNB", 2, 0.003, 600, 0.001, tx="d", ok=True),  # dup BNB deduped
    ]
    assert ra._nudged_tokens(swaps, "USDT") == ["BNB"]
