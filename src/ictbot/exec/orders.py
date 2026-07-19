"""
Order types + state machine.

Designed for stop-loss / take-profit bracket orders since that's all the
strategy emits. Spot / margin / contract details are deliberately not
modelled — venue-specific quirks live on each LiveBroker, not here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

Side = Literal["BUY", "SELL"]
Status = Literal["NEW", "OPEN", "FILLED", "CANCELLED", "REJECTED"]


@dataclass
class Order:
    """A single bracket order: entry @ market, with SL and TP."""

    pair: str
    side: Side
    entry: float
    sl: float
    tp: float
    qty: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: Status = "NEW"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: datetime | None = None
    closed_at: datetime | None = None
    close_price: float | None = None
    close_reason: str | None = None  # "TP" | "SL" | "MANUAL"
    # C1 (ROADMAP §C1): exchange-side order IDs for the bracket legs.
    # Populated by the live broker's place_order so cancel() can rip
    # them down atomically. None on paper-broker orders.
    entry_order_id: str | None = None
    sl_order_id: str | None = None
    tp_order_id: str | None = None
    # Fix 2.F (plan: live P&L clean-up): sum of entry-leg fee +
    # closing-leg fee in quote currency (USDT for USDT-M perps). Set
    # by the live broker's `_finalize_filled` from ccxt's `order["fee"]`.
    # None on paper-broker orders (where fees are modelled in the
    # backtest engine's friction_pct, not on the Order itself).
    fees_paid: float | None = None
    # Fix 5.B (plan: Phase 5 — close known gaps): True iff this Order
    # was rebuilt by BinanceLiveBroker.on_reconnect from `fetch_positions`
    # rather than placed by the bot. Reconciled stubs have approximate
    # sl/tp (recovered from open orders if visible, else derived from
    # SL_FRAC/TP_FRAC) — downstream consumers can use this to weight
    # the row's R for reporting.
    is_reconciled: bool = False

    def is_open(self) -> bool:
        return self.status in ("NEW", "OPEN")

    def realised_pnl_R(self) -> float | None:
        """Net P&L in R-multiples once closed, else None.

        Subtracts `fees_paid` (when present) translated to R via
        `fees / (qty × risk_distance)`. This is the live broker's
        fee-inclusive truth; paper / pre-fix orders with fees_paid=None
        return the legacy formula bit-for-bit.
        """
        if self.status != "FILLED" or self.close_price is None:
            return None
        risk = abs(self.entry - self.sl)
        if risk == 0:
            return 0.0
        if self.side == "BUY":
            gross_r = (self.close_price - self.entry) / risk
        else:
            gross_r = (self.entry - self.close_price) / risk
        if self.fees_paid is None or self.qty <= 0:
            return gross_r
        fees_r = self.fees_paid / (self.qty * risk)
        return gross_r - fees_r
