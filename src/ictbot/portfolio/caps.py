"""
Risk caps — the bouncers between Strategy and Broker.

Each cap is a small object that answers `allow(...)`. The orchestrator
asks every cap before placing an order. If any cap returns False with a
reason, the order is rejected and logged.

Caps are deliberately tiny and composable so adding new ones (max
concurrent shorts, per-pair limits, time-of-day blocks) is a one-class
PR rather than threading more if-statements through the orchestrator.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ictbot.exec.orders import Order
from ictbot.portfolio.account import Account

log = logging.getLogger(__name__)


@dataclass
class CapDecision:
    allow: bool
    reason: str = ""


# -- Caps -------------------------------------------------------------------


class MaxOpenPositions:
    """Reject if N or more positions are already open."""

    def __init__(self, max_open: int = 1) -> None:
        self.max_open = max_open

    def check(self, *, open_orders: list[Order], **_) -> CapDecision:
        if len(open_orders) >= self.max_open:
            return CapDecision(
                False,
                f"max_open_positions ({self.max_open}) reached ({len(open_orders)} currently open)",
            )
        return CapDecision(True)


class MaxConcurrentSameDirection:
    """Fix 9.B (plan: Phase 9 per-token completeness).

    Reject when `max_same` same-direction positions are already open. The
    portfolio still allows multiple concurrent positions (raised from 1 to
    3 in Phase 9.B) but not all of them in the same direction — prevents
    stacking 3 SELLs on correlated crypto pairs during a downtrend, which
    is the failure mode we observed when XRP/BTC/PAXG all fired SELL
    within minutes of each other.

    A `None` side means "no signal to evaluate" (cap is a no-op for that
    call). Same-direction count uses the canonical `order.side` of each
    open order.
    """

    def __init__(self, max_same: int = 2) -> None:
        self.max_same = int(max_same)

    def check(
        self,
        *,
        open_orders: list[Order],
        side: str | None = None,
        **_,
    ) -> CapDecision:
        if side is None or self.max_same <= 0:
            return CapDecision(True)
        same = sum(1 for o in open_orders if getattr(o, "side", None) == side)
        if same >= self.max_same:
            return CapDecision(
                False,
                f"max_same_direction ({self.max_same}) reached ({same} {side} currently open)",
            )
        return CapDecision(True)


class DailyLossLimit:
    """Reject once today's realised loss has reached `limit_R` (negative R)."""

    def __init__(self, limit_R: float = 2.0) -> None:
        self.limit_R = abs(limit_R)
        self._today: date | None = None
        self._today_loss_R = 0.0

    def record(self, r_multiple: float, when: datetime | None = None) -> None:
        when = when or datetime.now(timezone.utc)
        if self._today != when.date():
            self._today = when.date()
            self._today_loss_R = 0.0
        if r_multiple < 0:
            self._today_loss_R += abs(r_multiple)

    def check(self, **_) -> CapDecision:
        if self._today_loss_R >= self.limit_R:
            return CapDecision(
                False,
                f"daily_loss_limit ({self.limit_R}R) hit (today's loss: {self._today_loss_R:.2f}R)",
            )
        return CapDecision(True)


class MaxDrawdown:
    """Reject once the account drawdown crosses `limit` (fraction, 0..1)."""

    def __init__(self, account: Account, limit: float = 0.20) -> None:
        self.account = account
        self.limit = limit

    def check(self, **_) -> CapDecision:
        dd = self.account.drawdown
        if dd >= self.limit:
            return CapDecision(
                False,
                f"max_drawdown ({self.limit:.0%}) breached (current: {dd:.1%})",
            )
        return CapDecision(True)


class MaxLiveTradesPerDay:
    """Reject when today's placed entries (UTC) reach `limit`.

    Source of truth is the signals journal: every time the live router
    places a bracket it calls `append_signal()` with `entry` set to
    "BUY" or "SELL". This cap counts those for today's UTC date and
    rejects new ones above the limit. Reading from the journal means
    the count survives process restarts naturally — no extra state file.
    """

    def __init__(
        self,
        limit: int = 3,
        journal_reader: Callable[[], list] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.limit = int(limit)
        # Injectable for tests; production wiring uses read_journal.
        if journal_reader is None:
            from ictbot.portfolio.journal import read_journal

            journal_reader = lambda: read_journal()
        self._read = journal_reader
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _count_today(self) -> int:
        today = self._now().date()
        entries = self._read()
        n = 0
        for e in entries:
            if e.get("entry") not in ("BUY", "SELL"):
                continue
            ts = e.get("ts")
            if not ts:
                continue
            try:
                ts_dt = datetime.fromisoformat(ts)
            except (TypeError, ValueError):
                continue
            if ts_dt.date() == today:
                n += 1
        return n

    def check(self, **_) -> CapDecision:
        # Fix 15.A (plan: Phase 15 testing-phase relaxation): limit <= 0
        # means "no cap". Mirrors MaxConcurrentSameDirection's disabled
        # semantic. Short-circuits the journal-count read entirely so
        # the disabled state doesn't pay the I/O cost every cycle. Set
        # MAX_LIVE_TRADES_PER_DAY=0 in .env to trust every conf=100
        # signal during the testing-phase observation window.
        if self.limit <= 0:
            return CapDecision(True)
        try:
            count = self._count_today()
        except Exception as exc:
            log.warning("MaxLiveTradesPerDay: journal read failed (%s); allowing", exc)
            return CapDecision(True)
        if count >= self.limit:
            return CapDecision(
                False,
                f"max_live_trades_per_day ({self.limit}) reached (today: {count})",
            )
        return CapDecision(True)


class NearPriceDedup:
    """Reject a new entry when a recently-PLACED entry on the same
    (pair, side) sits within `threshold_bps` of the current price AND
    inside `window_seconds`. Stops the scanner from stacking duplicate
    trades on noisy near-identical prints of the same setup, while
    leaving a re-setup hours later (price has drifted) free to fire.

    Source of truth is the signals journal (same as MaxLiveTradesPerDay
    — survives restarts naturally). Only entries with `entry in
    {"BUY","SELL"}` count; REJECTED rows do NOT seed dedup because no
    actual trade happened at that price.

    Threshold is in basis points so the same number works across pairs
    of wildly different price scales:
       20 bps = 0.20 % → $3.10 on ETH $1,550 / $0.002 on XRP $1.08 /
       $0.12 on SOL $62 / $120 on BTC $60,000.

    `threshold_bps <= 0` or `window_seconds <= 0` disables the cap.
    """

    def __init__(
        self,
        threshold_bps: float = 20.0,
        window_seconds: float = 900.0,
        journal_reader: Callable[[], list] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.threshold_bps = float(threshold_bps)
        self.window_seconds = float(window_seconds)
        if journal_reader is None:
            from ictbot.portfolio.journal import read_journal

            journal_reader = lambda: read_journal()
        self._read = journal_reader
        self._now = now or (lambda: datetime.now(timezone.utc))

    def check(
        self,
        *,
        pair: str | None = None,
        side: str | None = None,
        price: float | None = None,
        **_,
    ) -> CapDecision:
        if self.threshold_bps <= 0 or self.window_seconds <= 0:
            return CapDecision(True)
        if pair is None or side not in ("BUY", "SELL") or price is None:
            return CapDecision(True)
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            return CapDecision(True)
        if price_f <= 0:
            return CapDecision(True)
        try:
            entries = self._read()
        except Exception as exc:
            log.warning("NearPriceDedup: journal read failed (%s); allowing", exc)
            return CapDecision(True)

        cutoff = self._now() - timedelta(seconds=self.window_seconds)
        for e in entries:
            if e.get("entry") != side:
                continue
            if e.get("pair") != pair:
                continue
            ts = e.get("ts")
            if not ts:
                continue
            try:
                ts_dt = datetime.fromisoformat(ts)
            except (TypeError, ValueError):
                continue
            if ts_dt < cutoff:
                continue
            e_price = e.get("price")
            try:
                e_price_f = float(e_price)
            except (TypeError, ValueError):
                continue
            if e_price_f <= 0:
                continue
            diff_bps = abs(price_f - e_price_f) / e_price_f * 10_000.0
            if diff_bps <= self.threshold_bps:
                age_s = (self._now() - ts_dt).total_seconds()
                return CapDecision(
                    False,
                    f"near_price_dedup ({diff_bps:.1f} bps from {side} @ "
                    f"{e_price_f:g} placed {age_s:.0f}s ago, threshold "
                    f"{self.threshold_bps:.0f} bps within {self.window_seconds:.0f}s)",
                )
        return CapDecision(True)


class NewsBlackoutCap:
    """Reject when a high-impact macro event is within ±window_minutes.

    Reuses the existing news cache (`ictbot.runtime.news.is_blackout`)
    that's warmed once per scanner loop at scanner.py:518. Fail-open if
    the cache lookup raises (cold start, fetcher failed) — better to
    miss a blackout than freeze all trading on a feed outage. The
    failure is logged once per check so it isn't silent.
    """

    def __init__(
        self,
        window_minutes: float = 30.0,
        countries: tuple[str, ...] = ("USD",),
        impacts: tuple[str, ...] = ("High",),
        is_blackout_fn: Callable[..., object] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.window_minutes = float(window_minutes)
        self.countries = tuple(countries) if countries else ("USD",)
        self.impacts = tuple(impacts) if impacts else ("High",)
        if is_blackout_fn is None:
            from ictbot.runtime.news import is_blackout

            is_blackout_fn = is_blackout
        self._is_blackout = is_blackout_fn
        self._now = now or (lambda: datetime.now(timezone.utc))

    def check(self, **_) -> CapDecision:
        if self.window_minutes <= 0:
            return CapDecision(True)
        try:
            hit = self._is_blackout(
                self.window_minutes,
                country=self.countries,
                impact=self.impacts,
                now=self._now(),
            )
        except Exception as exc:
            log.warning("NewsBlackoutCap: feed lookup failed (%s); allowing", exc)
            return CapDecision(True)
        if hit is None:
            return CapDecision(True)
        title = getattr(hit, "title", "event")
        return CapDecision(
            False,
            f"news_blackout (±{self.window_minutes:.0f}m of {title})",
        )


# -- Composition -------------------------------------------------------------


class CapGate:
    """Run a list of caps; first failure short-circuits.

    `**ctx` carries optional signal context (e.g. `side="BUY"|"SELL"`)
    that some caps need but others don't — every cap already accepts
    `**_` so unknown keys are ignored. Fix 9.B added `side` so
    MaxConcurrentSameDirection could distinguish BUY vs SELL stacks.
    """

    def __init__(self, caps: list) -> None:
        self.caps = caps

    def evaluate(self, *, open_orders: list[Order], **ctx) -> CapDecision:
        for cap in self.caps:
            d = cap.check(open_orders=open_orders, **ctx)
            if not d.allow:
                return d
        return CapDecision(True)
