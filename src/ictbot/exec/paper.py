"""
PaperBroker — simulated fills.

Fill model (deliberately simple, deterministic):
  - place_order(): the order is filled IMMEDIATELY at the order.entry
    price. Status → OPEN. (Phase 9 may add a "next bar's open" model.)
  - on_bar(pair, bar): for every OPEN order on that pair, if the bar
    range crosses SL or TP, close at the touched level.

Slippage / fees are NOT modelled here — those belong in the friction
math that the backtest engine already applies (FEE_PER_SIDE,
SLIPPAGE_PER_SIDE in settings). The paper broker is for live-shape
testing of the orchestrator wiring, not for second-guessing backtest
results.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from ictbot.exec.orders import Order


class PaperBroker:
    name = "paper"

    def __init__(
        self,
        on_close: Callable[[Order], None] | None = None,
        *,
        starting_balance: float = 10_000.0,
    ) -> None:
        self._orders: dict[str, Order] = {}
        # Audit gap #1: brokers must publish close events so caps can
        # record realised R-multiples + the account can update equity.
        # Default no-op so existing callers keep working; the router
        # supplies one in production.
        self._on_close = on_close
        # J11 (audit gap #19): brokers report their own equity so the
        # router doesn't trust a hard-coded number. Paper broker carries
        # a simulated balance updated from close events; live brokers
        # poll fetch_balance.
        self._balance = float(starting_balance)

    def equity(self) -> float:
        """Current account equity in quote currency."""
        return self._balance

    # ---- Broker protocol -------------------------------------------------
    def place_order(self, order: Order) -> Order:
        order.status = "OPEN"
        order.filled_at = datetime.now(timezone.utc)
        self._orders[order.id] = order
        return order

    def cancel(self, order_id: str) -> bool:
        o = self._orders.get(order_id)
        if not o or not o.is_open():
            return False
        o.status = "CANCELLED"
        o.closed_at = datetime.now(timezone.utc)
        return True

    def positions(self) -> list[Order]:
        return [o for o in self._orders.values() if o.is_open()]

    # ---- Test/sim driver -------------------------------------------------
    def on_bar(self, pair: str, bar: dict) -> list[Order]:
        """Feed a single OHLCV bar. Returns the orders this bar closed."""
        closed = []
        for o in list(self._orders.values()):
            if o.pair != pair or not o.is_open():
                continue
            hi, lo = bar["high"], bar["low"]
            if o.side == "BUY":
                if lo <= o.sl:
                    self._close(o, o.sl, "SL")
                elif hi >= o.tp:
                    self._close(o, o.tp, "TP")
            else:  # SELL
                if hi >= o.sl:
                    self._close(o, o.sl, "SL")
                elif lo <= o.tp:
                    self._close(o, o.tp, "TP")
            if not o.is_open():
                closed.append(o)
        return closed

    def _close(self, order: Order, price: float, reason: str) -> None:
        order.status = "FILLED"
        order.closed_at = datetime.now(timezone.utc)
        order.close_price = price
        order.close_reason = reason
        # Publish close → caps/account update. Wrapped because a callback
        # raising must not corrupt broker state (the order IS closed
        # regardless of whether the cap layer can record it).
        if self._on_close is not None:
            try:
                self._on_close(order)
            except Exception:  # noqa: BLE001 — broker must never propagate
                pass
