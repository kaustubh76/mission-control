"""
Broker protocol.

Every broker (paper, Binance live, Delta live, …) implements:
  - place_order(order)  → returns the (possibly mutated) Order
  - cancel(order_id)    → cancel an open order
  - positions()         → list of currently open Orders
  - on_bar(pair, bar)   → optional: feed a fresh OHLCV bar so the broker
                          can fill resting orders / hit SL / TP

The Strategy stays purely advisory: it emits Signals. An `Orchestrator`
(Phase 8.5) bridges Strategy → Broker, enforcing portfolio caps along
the way.
"""

from __future__ import annotations

from typing import Protocol

from ictbot.exec.orders import Order


class Broker(Protocol):
    name: str

    def place_order(self, order: Order) -> Order: ...
    def cancel(self, order_id: str) -> bool: ...
    def positions(self) -> list[Order]: ...
