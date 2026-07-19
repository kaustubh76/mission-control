"""
Tick-size aware price rounding.

Default behaviour (when no tick_size is supplied) preserves the legacy
`round(price, 2)` so callers that haven't migrated yet keep the same
output. Phase 6 introduces tick_size; Phase 8 will read it from
`exchange.market(symbol)['precision']['price']`.

This also fixes the empirical XRP loss documented in docs/findings.md
where 2-decimal rounding inflated the SL distance enough to push
friction above 70 % of risk on a 0.3 % stop.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal


def round_to_tick(price: float, tick_size: float | None = None) -> float:
    """Round `price` to the nearest multiple of `tick_size`.

    `tick_size=None` falls back to `round(price, 2)` (the legacy default).
    Uses banker's rounding via Decimal so floating-point error doesn't
    nudge values across a tick boundary in surprising ways.
    """
    if tick_size is None or tick_size <= 0:
        return round(price, 2)

    p = Decimal(str(price))
    t = Decimal(str(tick_size))
    n = (p / t).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return float(n * t)
