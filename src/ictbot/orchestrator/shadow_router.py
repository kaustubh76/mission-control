"""
Phase B — ShadowRouter (legacy ictbot).

Wraps two SignalRouter instances so the scanner can run a real live
broker AND a paper broker in parallel against the same signal stream.
The live router places real orders at `risk_pct_live`; the shadow
router runs PaperBroker at the normal `risk_pct` so the comparison
holds "what would the strategy have done at normal size" against the
real-friction live execution.

Failure semantics:
- The LIVE leg's exceptions propagate to the caller exactly as a plain
  SignalRouter's would — including LiveTradingDisabled — so the
  scanner's kill-switch / safety paths still trigger.
- The SHADOW leg's exceptions are swallowed and counted. A bug on the
  paper side must NEVER prevent live execution from completing.

Comparison instrumentation:
- shadow_fill_slippage_bps{pair, side} when both legs place.
- shadow_diverged_total{pair, reason} when only one leg places (or
  when the shadow leg raises).

For settlement, ShadowRouter exposes a `.broker` attribute that fans
`on_bar` out to both brokers but exposes the LIVE broker's `positions`
and `equity` to the scanner — so the scanner's existing settlement +
cap-gate plumbing keeps working unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ictbot.runtime import metrics

if TYPE_CHECKING:
    from ictbot.exec.orders import Order
    from ictbot.orchestrator.router import SignalRouter

log = logging.getLogger(__name__)


class _FanoutBroker:
    """Scanner-facing facade. Routes `on_bar` to BOTH the live and
    shadow brokers each closed bar; exposes ONLY the live broker's
    positions/equity to upstream callers.

    Why: scanner._settle_broker_on_last_closed_bar calls
    `router.broker.on_bar(pair, bar)` to drive paper TP/SL fills and
    live position reconciliation. We need both legs settled on the
    same bar without duplicating the settlement call site.
    """

    def __init__(self, live_broker, shadow_broker) -> None:
        self._live = live_broker
        self._shadow = shadow_broker
        # Compose the name so log lines like "router using broker=..."
        # show both legs are wired.
        self.name = f"{live_broker.name}+shadow"

    def on_bar(self, pair: str, bar: dict) -> list[Order]:
        """Fan-out: run live reconciliation first (it's the source of
        truth for the open-positions cap), then settle paper TP/SL.
        Returns only the LIVE-side closes — the scanner logs/journals
        those. Shadow closes flow through PaperBroker._on_close which
        the shadow router already wired internally.
        """
        live_closed = self._safe_on_bar(self._live, pair, bar, "live") or []
        self._safe_on_bar(self._shadow, pair, bar, "shadow")
        return live_closed

    @staticmethod
    def _safe_on_bar(broker, pair: str, bar: dict, leg: str) -> list:
        try:
            return broker.on_bar(pair, bar) or []
        except Exception as exc:
            log.warning("%s broker on_bar(%s) raised: %s", leg, pair, exc)
            return []

    # The scanner reads positions() to feed CapGate. Use the LIVE side
    # so a paper fill never blocks a real entry, and vice versa — each
    # leg's CapGate is interrogated independently inside route().
    def positions(self):
        return self._live.positions()

    def equity(self) -> float:
        if hasattr(self._live, "equity"):
            try:
                return float(self._live.equity())
            except Exception as exc:
                log.warning("live broker equity() raised: %s", exc)
        return 0.0


class ShadowRouter:
    """Composes a live SignalRouter and a shadow SignalRouter."""

    def __init__(
        self,
        live_router: SignalRouter,
        shadow_router: SignalRouter,
    ) -> None:
        self.live_router = live_router
        self.shadow_router = shadow_router
        # Exposed for the scanner's settlement + cap-gate plumbing.
        # cap_gate mirrors the live side so existing _build_router
        # logging (caps count) stays meaningful.
        self.broker = _FanoutBroker(live_router.broker, shadow_router.broker)
        self.cap_gate = live_router.cap_gate

    def route(self, result: dict):
        """Run the shadow leg first (best effort), then the live leg
        (raises propagate). Compare outcomes and emit comparison
        metrics. Returns the live RouteOutcome.
        """
        pair = result.get("pair", "?")

        # ---- shadow leg (best effort) ------------------------------------
        shadow_outcome = None
        try:
            shadow_outcome = self.shadow_router.route(result)
        except Exception as exc:
            log.warning("shadow router raised (swallowed): %s", exc)
            metrics.shadow_diverged_total.labels(pair=pair, reason="shadow_exception").inc()

        # ---- live leg (raises propagate) ---------------------------------
        live_outcome = self.live_router.route(result)

        # ---- comparison metrics ------------------------------------------
        self._emit_comparison(result, live_outcome, shadow_outcome)
        return live_outcome

    def _emit_comparison(self, result, live_outcome, shadow_outcome) -> None:
        if live_outcome is None or shadow_outcome is None:
            return
        pair = result.get("pair", "?")
        live_placed = bool(getattr(live_outcome, "placed", False))
        shadow_placed = bool(getattr(shadow_outcome, "placed", False))

        if live_placed and shadow_placed:
            signal_px = float(result.get("price") or 0.0)
            live_order = getattr(live_outcome, "order", None)
            if signal_px > 0 and live_order is not None:
                fill_px = float(getattr(live_order, "entry", 0.0))
                if fill_px > 0:
                    raw_bps = (fill_px - signal_px) / signal_px * 10_000
                    # Normalise sign: positive bps always means "worse
                    # fill". For SELL, getting a higher fill price is
                    # actually BETTER, so flip the sign.
                    side = getattr(live_order, "side", "BUY")
                    slip_bps = raw_bps if side == "BUY" else -raw_bps
                    metrics.shadow_fill_slippage_bps.labels(pair=pair, side=side).observe(slip_bps)
        elif live_placed and not shadow_placed:
            metrics.shadow_diverged_total.labels(pair=pair, reason="shadow_rejected").inc()
        elif shadow_placed and not live_placed:
            metrics.shadow_diverged_total.labels(pair=pair, reason="live_rejected").inc()
        # Both rejected — not a divergence, just a quiet cycle.
