"""
C2 (ROADMAP §C2) — SignalRouter.

Today the path looks like:

    analyze_pair → telegram alert + journal append

The Strategy emits a result dict; nothing actually places an order on
any broker. SignalRouter inserts the missing layer:

    Signal (BUY/SELL from Strategy)
        → CapGate.evaluate(open_orders=broker.positions())
            → if allowed: broker.place_order(order)
                → journal.append + notifier.send (if configured)

CapGate failures are journalled as REJECTED so the dashboard can show
why a setup didn't make it to a position.

The router does not own market-data fetching or the scanning loop —
that's still the scanner's job. The router is invoked per-signal.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ictbot.exec.orders import Order
from ictbot.portfolio.account import Account
from ictbot.portfolio.caps import CapDecision, CapGate, DailyLossLimit

log = logging.getLogger(__name__)


class _BrokerLike(Protocol):
    def place_order(self, order: Order) -> Order: ...
    def positions(self) -> list[Order]: ...


@dataclass
class RouteOutcome:
    """What happened when we routed a signal."""

    placed: bool
    order: Order | None
    rejection: CapDecision | None
    reason: str = ""


def _qty_for_risk(*, balance: float, risk_pct: float, entry: float, sl: float) -> float:
    """Position size such that a stop-out loses `balance * risk_pct`."""
    risk_distance = abs(entry - sl)
    if risk_distance <= 0:
        return 0.0
    risk_dollars = balance * risk_pct
    return round(risk_dollars / risk_distance, 6)


def _floor_to_step(value: float, step: float) -> float:
    """J2 (audit gap #10): floor `value` to the nearest multiple of `step`.

    Binance BTC step = 0.001; ETH = 0.01; Delta = 1.0. Posting a non-step
    quantity is exchange-rejected. `step <= 0` is treated as "no step".
    """
    import math

    if step is None or step <= 0:
        return value
    return math.floor(value / step) * step


class SignalRouter:
    """Glue between Strategy output and the Broker.

    Construction:
      router = SignalRouter(
          broker=PaperBroker(),
          cap_gate=CapGate([MaxOpenPositions(1), DailyLossLimit(1.0)]),
          balance=10_000,
          risk_pct=0.005,
      )

    Usage:
      result = strategy.evaluate(...)  # the existing dict from analyzer
      outcome = router.route(result)
      if outcome.placed:
          ...
    """

    def __init__(
        self,
        broker: _BrokerLike,
        cap_gate: CapGate | None = None,
        *,
        balance: float = 10_000.0,
        risk_pct: float = 0.005,
        notifier: Callable[[str], None] | None = None,
        journal: Callable[..., None] | None = None,
        account: Account | None = None,
        is_live: bool = False,
    ) -> None:
        self.broker = broker
        self.cap_gate = cap_gate or CapGate([])
        self.balance = balance
        self.risk_pct = risk_pct
        self.notifier = notifier
        self.journal = journal
        # Phase D: when True, every successful place_order() increments
        # `ictbot_live_trades_total`. Shadow + paper-only routers leave
        # this False so they don't pollute the live-fill counter.
        self.is_live = bool(is_live)
        # Audit gap #1: keep an Account so MaxDrawdown actually sees R-deltas,
        # and let close events feed DailyLossLimit. Pre-existing tests can
        # pass a None account; the on_close hook will then skip equity
        # bookkeeping but still feed DailyLossLimit instances on the gate.
        self.account = account
        # Best-effort wire-through: if the broker accepts a close callback
        # (PaperBroker + live brokers do), point it at our on_close.
        # Brokers without the attribute are left alone — caller is
        # responsible for calling router.on_close directly when their
        # broker closes a position.
        if hasattr(broker, "_on_close") and getattr(broker, "_on_close", None) is None:
            broker._on_close = self.on_close
        # Fix 5.E: dedup counter for the throttled TG rejection summary.
        # Keyed by (pair, reason_head). Resets only when the process
        # restarts — acceptable for the visibility intent.
        self._rejection_counts: dict[tuple[str, str], int] = {}

    # ---- exchange-precision lookups (J2) ------------------------------------

    def _lookup_qty_step(self, pair: str) -> float | None:
        """Return the exchange's qty step for `pair`. Brokers expose
        `qty_step` directly (live) or via a delegated exchange handle
        (paper). Returns None if no source is reachable."""
        broker_step = getattr(self.broker, "qty_step", None)
        if callable(broker_step):
            try:
                return float(broker_step(pair))
            except Exception:
                pass
        # Fall back to the factory's default exchange (paper-only path).
        try:
            from ictbot.data.factory import get_default_exchange

            return float(get_default_exchange().qty_step(pair))
        except Exception:
            return None

    def _lookup_min_notional(self, pair: str) -> float:
        """Same shape as _lookup_qty_step but for min order value."""
        broker_min = getattr(self.broker, "min_notional", None)
        if callable(broker_min):
            try:
                return float(broker_min(pair))
            except Exception:
                pass
        try:
            from ictbot.data.factory import get_default_exchange

            return float(get_default_exchange().min_notional(pair))
        except Exception:
            return 0.0

    # ---- close-event handler -------------------------------------------------

    def on_close(self, order: Order) -> None:
        """Audit gap #1: cap layer + account need close events.

        For every closed order, push its realised R into each DailyLossLimit
        on the gate and into the Account (if any). Idempotent enough: an
        order is closed exactly once by the broker, so we'll see each close
        once per process lifetime.

        J1 (audit gap #9): also update the journal so the broker stays
        the single source of close-state truth — the journal becomes a
        read-only mirror instead of a parallel state.

        Fix 5.C (plan: Phase 5 Tier 2): after the journal write, emit a
        TG message summarising the close so the operator doesn't have
        to poll. Gated on `TG_NOTIFY_ON_CLOSE` (default True).
        """
        r = order.realised_pnl_R()
        if r is None:
            return
        for cap in self.cap_gate.caps:
            if isinstance(cap, DailyLossLimit):
                cap.record(r, when=order.closed_at)
        if self.account is not None:
            self.account.book_close(r, risk_pct=self.risk_pct)
        # Mirror to journal — best-effort, never blocks cap/account update.
        try:
            from ictbot.portfolio.journal import mark_closed_from_broker

            mark_closed_from_broker(order)
        except Exception as exc:
            log.warning("journal mirror failed for %s: %s", order.id, exc)
        # Fix 5.C: TG close notify.
        try:
            from ictbot.settings import TG_NOTIFY_ON_CLOSE

            if TG_NOTIFY_ON_CLOSE and self.is_live:
                self._tg_notify_close(order, r)
        except Exception as exc:
            log.warning("TG close notify failed for %s: %s", order.id, exc)

    @staticmethod
    def _tg_notify_close(order: Order, realised_r: float) -> None:
        """Send a one-line TG close summary. Pulls send_telegram lazily
        so test runners without TG creds don't blow up at import time.
        """
        from ictbot.notify.telegram import send_telegram

        reason = (order.close_reason or "?").upper()
        fees = f"fees=${order.fees_paid:.4f}" if order.fees_paid is not None else "fees=n/a"
        msg = (
            f"CLOSE {order.pair} {order.side} reason={reason}\n"
            f"entry={order.entry:.4f} exit={order.close_price:.4f} qty={order.qty}\n"
            f"R={realised_r:+.3f} {fees}"
        )
        if order.is_reconciled:
            msg = "[reconciled stub] " + msg
        send_telegram(msg)

    def route(self, result: dict) -> RouteOutcome:
        """Route a Strategy result dict. Returns a RouteOutcome."""
        entry = result.get("entry")
        if entry not in ("BUY", "SELL"):
            return RouteOutcome(False, None, None, "no signal")

        # Caps ask the broker what's currently open. That's the live source
        # of truth — never trust a cached "I think there are N open" because
        # an external fill could have closed one between iterations.
        open_orders = self.broker.positions()
        # Fix 9.B (plan: Phase 9): forward `side` so MaxConcurrentSameDirection
        # can count BUY vs SELL separately. Caps that don't need it ignore
        # via **_.
        # Phase 14: forward `pair` + `price` so NearPriceDedup can compare
        # the current signal against recent PLACED entries in the journal.
        try:
            signal_price = float(result["price"]) if result.get("price") is not None else None
        except (TypeError, ValueError):
            signal_price = None
        decision = self.cap_gate.evaluate(
            open_orders=open_orders,
            side=entry,
            pair=result.get("pair"),
            price=signal_price,
        )
        if not decision.allow:
            log.info("Signal rejected by cap gate: %s", decision.reason)
            self._journal_rejected(result, decision.reason)
            return RouteOutcome(False, None, decision, decision.reason)

        # J11 (audit gap #19): prefer the broker's live equity. PaperBroker
        # carries a simulated number; live brokers read fetch_balance.
        # Falls back to the constructor's `balance` (legacy callers).
        equity = self.balance
        if hasattr(self.broker, "equity"):
            try:
                live_equity = self.broker.equity()
                if live_equity > 0:
                    equity = float(live_equity)
            except Exception as exc:
                log.warning("broker.equity() raised, falling back: %s", exc)

        pair_id = result.get("pair", "UNKNOWN")
        entry_px = float(result["price"])
        sl_px = float(result["sl"])
        tp_px = float(result["tp"])
        raw_qty = _qty_for_risk(balance=equity, risk_pct=self.risk_pct, entry=entry_px, sl=sl_px)

        # J2 (audit gap #10): floor qty to the exchange's step. Posting a
        # non-step amount is rejected by Binance/Delta. Step lookup is
        # best-effort — brokers/exchanges without a step return None and
        # qty stays at its raw value.
        qty = raw_qty
        try:
            step = self._lookup_qty_step(pair_id)
            qty = _floor_to_step(qty, step) if step else qty
        except Exception as exc:
            log.warning("qty_step lookup failed for %s: %s", pair_id, exc)

        # J2: reject sub-min-notional. Returns a CapDecision-style outcome
        # so the dashboard can show *why* a setup didn't place.
        try:
            min_n = self._lookup_min_notional(pair_id)
        except Exception:
            min_n = 0.0
        notional = qty * entry_px
        if min_n > 0 and notional < min_n:
            from ictbot.portfolio.caps import CapDecision

            reason = (
                f"min_notional ({min_n:.2f}) — sized notional={notional:.2f}, "
                f"qty={qty}, equity={equity:.2f}"
            )
            log.info("Signal rejected by min-notional: %s", reason)
            self._journal_rejected(result, reason)
            return RouteOutcome(False, None, CapDecision(False, reason), reason)

        order = Order(
            pair=pair_id,
            side=entry,
            entry=entry_px,
            sl=sl_px,
            tp=tp_px,
            qty=qty,
        )
        placed = self.broker.place_order(order)
        self._journal_placed(result, placed)
        self._notify(result, placed)
        # Phase D: count live placements only. Best-effort — a metrics
        # failure must never break the routing path.
        if self.is_live:
            try:
                from ictbot.runtime import metrics

                metrics.live_trades_total.labels(pair=pair_id, direction=entry).inc()
            except Exception as exc:
                log.warning("live_trades_total inc failed: %s", exc)
        return RouteOutcome(True, placed, None)

    # ---- side-effect helpers; quiet no-ops if no callback supplied -------

    def _journal_placed(self, result: dict, order: Order) -> None:
        if not self.journal:
            return
        try:
            self.journal(
                pair=order.pair,
                entry=order.side,
                price=order.entry,
                sl=order.sl,
                tp=order.tp,
                rr=float(result.get("rr") or 0.0),
                confidence=int(result.get("confidence") or 0),
                broker=getattr(self.broker, "name", "paper"),
                # Fix 16.A: persist active_session at signal-fire time
                # for the daily session-bucketed report.
                session=result.get("active_session"),
            )
        except Exception as exc:
            log.warning("Journal write failed: %s", exc)

    def _journal_rejected(self, result: dict, reason: str) -> None:
        if not self.journal:
            return
        try:
            # Same shape as a placed entry but with a sentinel REJECTED side
            # — keeps the journal append function simple (no second writer).
            self.journal(
                pair=result.get("pair", "UNKNOWN"),
                entry=f"REJECTED ({reason})",
                price=float(result.get("price") or 0.0),
                sl=float(result.get("sl") or 0.0),
                tp=float(result.get("tp") or 0.0),
                rr=float(result.get("rr") or 0.0),
                confidence=int(result.get("confidence") or 0),
                broker=getattr(self.broker, "name", "paper"),
                # Fix 16.A: same session tag on rejected rows so the
                # report can show cap-pressure per bucket.
                session=result.get("active_session"),
            )
        except Exception as exc:
            log.warning("Journal rejection write failed: %s", exc)
        # Fix 5.E (plan: Phase 5 Tier 2): throttled TG summary on the
        # Nth occurrence per (pair, reason). 0 (default) = silent.
        try:
            self._maybe_tg_notify_rejection(result, reason)
        except Exception as exc:
            log.warning("TG rejection notify failed: %s", exc)

    def _maybe_tg_notify_rejection(self, result: dict, reason: str) -> None:
        from ictbot.settings import TG_NOTIFY_REJECTIONS_EVERY

        threshold = int(TG_NOTIFY_REJECTIONS_EVERY)
        if threshold <= 0 or not self.is_live:
            return
        pair = result.get("pair", "UNKNOWN")
        # Strip parameters from the reason to keep the dedup key stable
        # (e.g. "max_open_positions (1) reached (1 currently open)" ->
        # "max_open_positions").
        head = reason.split(" ", 1)[0] if reason else "unknown"
        key = (pair, head)
        self._rejection_counts[key] = self._rejection_counts.get(key, 0) + 1
        n = self._rejection_counts[key]
        if n % threshold != 0:
            return
        from ictbot.notify.telegram import send_telegram

        send_telegram(
            f"REJECTED x{n} {pair} cap={head} price={result.get('price')} (every {threshold})"
        )

    def _notify(self, result: dict, order: Order) -> None:
        if not self.notifier:
            return
        try:
            msg = (
                f"{order.side} {order.pair} @ {order.entry} "
                f"SL={order.sl} TP={order.tp} (R:R={result.get('rr'):.2f})"
            )
            self.notifier(msg)
        except Exception as exc:
            log.warning("Notify failed: %s", exc)
