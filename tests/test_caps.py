"""Tests for portfolio risk caps — Phase 8 + Phase D."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ictbot.exec.orders import Order
from ictbot.portfolio.account import Account
from ictbot.portfolio.caps import (
    CapGate,
    DailyLossLimit,
    MaxConcurrentSameDirection,
    MaxDrawdown,
    MaxLiveTradesPerDay,
    MaxOpenPositions,
    NearPriceDedup,
    NewsBlackoutCap,
)


def _open_order():
    return Order(pair="BTC/USDT:USDT", side="BUY", entry=100, sl=95, tp=110, qty=1.0)


def test_max_open_positions_blocks_at_limit():
    cap = MaxOpenPositions(max_open=1)
    assert cap.check(open_orders=[]).allow is True
    decision = cap.check(open_orders=[_open_order()])
    assert decision.allow is False
    assert "max_open_positions" in decision.reason


def test_daily_loss_limit_accumulates_then_blocks():
    cap = DailyLossLimit(limit_R=2.0)
    assert cap.check().allow is True
    cap.record(-1.0)
    assert cap.check().allow is True
    cap.record(-1.5)  # cumulative loss = 2.5R > 2R limit
    decision = cap.check()
    assert decision.allow is False
    assert "daily_loss_limit" in decision.reason


def test_daily_loss_limit_only_counts_losses():
    cap = DailyLossLimit(limit_R=1.0)
    cap.record(3.0)  # a win — should NOT count
    assert cap.check().allow is True


def test_max_drawdown_blocks_when_breached():
    acc = Account(starting_balance=1000.0)
    cap = MaxDrawdown(account=acc, limit=0.05)
    # Hit 6 R-losses in a row → drawdown ≈ 6% > 5%.
    for _ in range(6):
        acc.book_close(-1.0)
    decision = cap.check()
    assert decision.allow is False
    assert "max_drawdown" in decision.reason


def test_cap_gate_short_circuits_on_first_failure():
    blocker = MaxOpenPositions(max_open=0)
    silent = MaxOpenPositions(max_open=99)
    gate = CapGate([blocker, silent])
    d = gate.evaluate(open_orders=[_open_order()])
    assert d.allow is False
    assert "max_open_positions (0)" in d.reason


# ---- Fix 9.B (Phase 9) MaxConcurrentSameDirection -------------------------


def _order(side: str, pair: str = "BTC/USDT:USDT") -> Order:
    return Order(pair=pair, side=side, entry=100, sl=95, tp=110, qty=1.0)


class TestMaxConcurrentSameDirection:
    def test_allows_when_no_side_in_context(self):
        cap = MaxConcurrentSameDirection(max_same=1)
        # No `side=` kwarg → cap is a no-op for that evaluation cycle.
        d = cap.check(open_orders=[_order("SELL"), _order("SELL")])
        assert d.allow is True

    def test_blocks_third_same_direction(self):
        cap = MaxConcurrentSameDirection(max_same=2)
        opens = [
            _order("SELL", "BTC/USDT:USDT"),
            _order("SELL", "XRP/USDT:USDT"),
        ]
        d = cap.check(open_orders=opens, side="SELL")
        assert d.allow is False
        assert "max_same_direction (2)" in d.reason
        assert "SELL currently open" in d.reason

    def test_allows_opposite_direction(self):
        cap = MaxConcurrentSameDirection(max_same=2)
        opens = [
            _order("SELL", "BTC/USDT:USDT"),
            _order("SELL", "XRP/USDT:USDT"),
        ]
        d = cap.check(open_orders=opens, side="BUY")
        assert d.allow is True

    def test_mixed_directions_count_separately(self):
        cap = MaxConcurrentSameDirection(max_same=2)
        opens = [
            _order("BUY", "BTC/USDT:USDT"),
            _order("SELL", "ETH/USDT:USDT"),
            _order("BUY", "SOL/USDT:USDT"),
        ]
        # 2 BUYs open → next BUY blocked.
        assert cap.check(open_orders=opens, side="BUY").allow is False
        # Only 1 SELL open → next SELL allowed.
        assert cap.check(open_orders=opens, side="SELL").allow is True

    def test_max_same_zero_disables(self):
        cap = MaxConcurrentSameDirection(max_same=0)
        # Even with many same-side opens, cap is off.
        opens = [_order("SELL") for _ in range(5)]
        assert cap.check(open_orders=opens, side="SELL").allow is True

    def test_integrates_with_cap_gate(self):
        """The CapGate.evaluate signature must forward `side=` through to
        the new cap. Other caps still ignore via **_."""
        gate = CapGate(
            [
                MaxOpenPositions(max_open=99),
                MaxConcurrentSameDirection(max_same=2),
            ]
        )
        opens = [_order("SELL"), _order("SELL")]
        # No side → only the open-positions cap runs (allows; 99 >> 2).
        assert gate.evaluate(open_orders=opens).allow is True
        # Side=SELL → same-direction cap rejects.
        d = gate.evaluate(open_orders=opens, side="SELL")
        assert d.allow is False
        assert "max_same_direction" in d.reason
        # Side=BUY → both caps allow.
        assert gate.evaluate(open_orders=opens, side="BUY").allow is True


# ---- Phase D caps ---------------------------------------------------------


def _journal_entry(ts_iso: str, entry: str = "BUY") -> dict:
    return {
        "ts": ts_iso,
        "pair": "BTC/USDT:USDT",
        "entry": entry,
        "price": 100.0,
        "sl": 95.0,
        "tp": 110.0,
        "rr": 2.0,
        "confidence": 100,
        "outcome": "OPEN",
        "closed_ts": None,
        "closed_price": None,
    }


def test_max_live_trades_per_day_allows_below_limit():
    fixed_now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    entries = [
        _journal_entry(fixed_now.replace(hour=9).isoformat()),
        _journal_entry(fixed_now.replace(hour=10).isoformat(), entry="SELL"),
    ]
    cap = MaxLiveTradesPerDay(
        limit=3,
        journal_reader=lambda: entries,
        now=lambda: fixed_now,
    )
    assert cap.check().allow is True


def test_max_live_trades_per_day_blocks_at_limit():
    fixed_now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    entries = [
        _journal_entry(fixed_now.replace(hour=9).isoformat()),
        _journal_entry(fixed_now.replace(hour=10).isoformat(), entry="SELL"),
        _journal_entry(fixed_now.replace(hour=11).isoformat()),
        # Yesterday's entry must NOT count.
        _journal_entry((fixed_now - timedelta(days=1)).isoformat()),
        # REJECTED rows must NOT count.
        _journal_entry(fixed_now.isoformat(), entry="REJECTED (cap)"),
    ]
    cap = MaxLiveTradesPerDay(
        limit=3,
        journal_reader=lambda: entries,
        now=lambda: fixed_now,
    )
    decision = cap.check()
    assert decision.allow is False
    assert "max_live_trades_per_day (3)" in decision.reason
    assert "today: 3" in decision.reason


# ---- Fix 15.A (Phase 15) testing-phase trade-count disabled semantic ----


def test_max_live_trades_per_day_disabled_by_zero():
    """Fix 15.A: limit=0 means 'no cap'. Even with many entries today,
    the cap must allow — and it must skip the journal read entirely
    (no I/O when the cap is intentionally off)."""
    fixed_now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    journal_calls = {"n": 0}

    def _spy_reader():
        journal_calls["n"] += 1
        return [_journal_entry(fixed_now.isoformat()) for _ in range(100)]

    cap = MaxLiveTradesPerDay(
        limit=0,
        journal_reader=_spy_reader,
        now=lambda: fixed_now,
    )
    decision = cap.check()
    assert decision.allow is True
    # Journal reader must NOT have been called — cap is short-circuited.
    assert journal_calls["n"] == 0


def test_max_live_trades_per_day_disabled_by_negative():
    """Negative limits also count as disabled (mirrors
    MaxConcurrentSameDirection's semantic)."""
    fixed_now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    cap = MaxLiveTradesPerDay(
        limit=-1,
        journal_reader=lambda: [_journal_entry(fixed_now.isoformat())] * 50,
        now=lambda: fixed_now,
    )
    assert cap.check().allow is True


# ---- NearPriceDedup (Phase 14) ------------------------------------------


def _dedup_entry(
    *,
    ts_iso: str,
    entry: str = "BUY",
    pair: str = "BTC/USDT:USDT",
    price: float = 100.0,
) -> dict:
    """Journal row shape NearPriceDedup actually reads."""
    return {
        "ts": ts_iso,
        "pair": pair,
        "entry": entry,
        "price": price,
        "outcome": "OPEN",
    }


class TestNearPriceDedup:
    NOW = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)

    def _cap(self, **kw):
        defaults = dict(
            threshold_bps=20.0,
            window_seconds=900.0,
            journal_reader=lambda: [],
            now=lambda: self.NOW,
        )
        defaults.update(kw)
        return NearPriceDedup(**defaults)

    def test_allows_when_no_recent_entries(self):
        cap = self._cap()
        d = cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.0)
        assert d.allow is True

    def test_blocks_within_threshold_and_window(self):
        # Same-pair, same-side BUY placed 1 minute ago at 100.0;
        # current price 100.10 → 10 bps drift, inside both knobs.
        recent = [
            _dedup_entry(
                ts_iso=(self.NOW - timedelta(minutes=1)).isoformat(),
                entry="BUY",
                price=100.0,
            )
        ]
        cap = self._cap(journal_reader=lambda: recent)
        d = cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.10)
        assert d.allow is False
        assert "near_price_dedup" in d.reason
        assert "10.0 bps" in d.reason

    def test_allows_beyond_bps_threshold(self):
        # Same-pair same-side but 50 bps away (well over 20).
        recent = [
            _dedup_entry(
                ts_iso=(self.NOW - timedelta(minutes=1)).isoformat(),
                entry="BUY",
                price=100.0,
            )
        ]
        cap = self._cap(journal_reader=lambda: recent)
        d = cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.50)
        assert d.allow is True

    def test_allows_beyond_window(self):
        # 30 minutes old → outside the 15-min window even though price is
        # bit-for-bit identical.
        old = [
            _dedup_entry(
                ts_iso=(self.NOW - timedelta(minutes=30)).isoformat(),
                entry="BUY",
                price=100.0,
            )
        ]
        cap = self._cap(journal_reader=lambda: old)
        d = cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.0)
        assert d.allow is True

    def test_allows_opposite_side(self):
        recent = [
            _dedup_entry(
                ts_iso=(self.NOW - timedelta(minutes=1)).isoformat(),
                entry="BUY",
                price=100.0,
            )
        ]
        cap = self._cap(journal_reader=lambda: recent)
        d = cap.check(pair="BTC/USDT:USDT", side="SELL", price=100.0)
        assert d.allow is True

    def test_allows_different_pair(self):
        recent = [
            _dedup_entry(
                ts_iso=(self.NOW - timedelta(minutes=1)).isoformat(),
                entry="BUY",
                price=100.0,
                pair="BTC/USDT:USDT",
            )
        ]
        cap = self._cap(journal_reader=lambda: recent)
        d = cap.check(pair="ETH/USDT:USDT", side="BUY", price=100.0)
        assert d.allow is True

    def test_skips_rejected_rows(self):
        # REJECTED row at same price + time should NOT seed dedup.
        rejected = [
            _dedup_entry(
                ts_iso=(self.NOW - timedelta(minutes=1)).isoformat(),
                entry="REJECTED (cap)",
                price=100.0,
            )
        ]
        cap = self._cap(journal_reader=lambda: rejected)
        d = cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.0)
        assert d.allow is True

    def test_disabled_when_threshold_is_zero(self):
        # Should short-circuit BEFORE reading the journal.
        calls = {"n": 0}

        def _spy():
            calls["n"] += 1
            return []

        cap = self._cap(threshold_bps=0.0, journal_reader=_spy)
        d = cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.0)
        assert d.allow is True
        assert calls["n"] == 0

    def test_disabled_when_window_is_zero(self):
        calls = {"n": 0}

        def _spy():
            calls["n"] += 1
            return []

        cap = self._cap(window_seconds=0.0, journal_reader=_spy)
        assert cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.0).allow is True
        assert calls["n"] == 0

    def test_allows_when_pair_missing(self):
        # No `pair` arg in context → cap is a no-op (can't compare).
        cap = self._cap()
        assert cap.check(side="BUY", price=100.0).allow is True

    def test_allows_when_side_not_directional(self):
        cap = self._cap()
        # `side` could be a REJECTED-string when called from non-router paths.
        assert cap.check(pair="BTC/USDT:USDT", side=None, price=100.0).allow is True
        assert cap.check(pair="BTC/USDT:USDT", side="REJECTED", price=100.0).allow is True

    def test_allows_when_price_missing(self):
        cap = self._cap()
        assert cap.check(pair="BTC/USDT:USDT", side="BUY", price=None).allow is True

    def test_journal_read_failure_allows_gracefully(self):
        # Don't let an I/O hiccup brick the scanner.
        def _boom():
            raise OSError("disk just exploded")

        cap = self._cap(journal_reader=_boom)
        d = cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.0)
        assert d.allow is True

    def test_skips_rows_with_missing_or_unparseable_ts(self):
        rows = [
            _dedup_entry(ts_iso="", price=100.0),
            {**_dedup_entry(ts_iso=self.NOW.isoformat()), "ts": None},
            {**_dedup_entry(ts_iso=self.NOW.isoformat()), "ts": "not-a-date"},
        ]
        cap = self._cap(journal_reader=lambda: rows)
        assert cap.check(pair="BTC/USDT:USDT", side="BUY", price=100.0).allow is True

    def test_integrates_with_cap_gate_via_pair_and_price(self):
        """CapGate.evaluate must forward pair + price kwargs through so
        the dedup cap can use them; other caps still ignore via **_."""
        recent = [
            _dedup_entry(
                ts_iso=(self.NOW - timedelta(minutes=1)).isoformat(),
                entry="BUY",
                price=100.0,
            )
        ]
        gate = CapGate(
            [
                MaxOpenPositions(max_open=99),
                NearPriceDedup(
                    threshold_bps=20.0,
                    window_seconds=900.0,
                    journal_reader=lambda: recent,
                    now=lambda: self.NOW,
                ),
            ]
        )
        d = gate.evaluate(
            open_orders=[],
            side="BUY",
            pair="BTC/USDT:USDT",
            price=100.05,
        )
        assert d.allow is False
        assert "near_price_dedup" in d.reason


def test_news_blackout_blocks_within_window():
    fixed_now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    event = SimpleNamespace(title="CPI", ts=fixed_now + timedelta(minutes=10))

    def fake_is_blackout(window_min, *, country, impact, now):
        return event

    cap = NewsBlackoutCap(
        window_minutes=30,
        is_blackout_fn=fake_is_blackout,
        now=lambda: fixed_now,
    )
    decision = cap.check()
    assert decision.allow is False
    assert "news_blackout" in decision.reason
    assert "CPI" in decision.reason


def test_news_blackout_allows_outside_window():
    fixed_now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)

    def fake_is_blackout(window_min, *, country, impact, now):
        return None  # event 90 min away → not in window

    cap = NewsBlackoutCap(
        window_minutes=30,
        is_blackout_fn=fake_is_blackout,
        now=lambda: fixed_now,
    )
    assert cap.check().allow is True


def test_news_blackout_disabled_when_window_zero():
    """window_minutes=0 → cap is a no-op even if events exist."""
    called = []

    def fake_is_blackout(*a, **kw):
        called.append(1)
        return SimpleNamespace(title="should-not-fire")

    cap = NewsBlackoutCap(window_minutes=0, is_blackout_fn=fake_is_blackout)
    assert cap.check().allow is True
    assert called == []  # fast-path skipped the lookup
