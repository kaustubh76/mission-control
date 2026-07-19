"""
DeltaLiveBroker — places REAL orders on Delta perpetuals (delta.exchange).

GATED by:
  1. `settings.enable_live_trading` must be True.
  2. The pair must be in the `allowed_pairs` set passed at construction.

If either check fails, `place_order` raises `LiveTradingDisabled` and
nothing is sent to ccxt.

Bracket placement:
  1. market entry on `order.side`
  2. reduce-only stop-market at `order.sl`
  3. reduce-only limit at `order.tp`

Audit gap #5 (bracket rollback) is honoured: any failure on leg 2 or 3
triggers an emergency reduce-only market flatten and re-raises. The
Order is registered in `self._orders` only when ALL three legs succeed.

Delta-specific quantization:
  - `order.qty` arrives from the router in *coin* units (e.g. 0.1 BTC).
    Delta's contract is fractional (BTC = 0.001 BTC/contract, ETH = 0.01,
    SOL/XRP = 1). We convert coin → contracts via `contract_size(symbol)`
    and floor to `qty_step(symbol)` (=1.0 on Delta) before placement.
  - Orders smaller than 1 contract are rejected upstream (the gate that
    decides "trade too small to take" should live in the router, not
    here — for now we floor to 1 to avoid silent drops).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime, timezone

import ccxt

from ictbot.exec.orders import Order
from ictbot.settings import settings as _settings

log = logging.getLogger(__name__)


class LiveTradingDisabled(RuntimeError):
    """Raised when live trading is requested but the kill switch is off."""


class DeltaLiveBroker:
    name = "delta-live"

    def __init__(
        self,
        allowed_pairs: set[str] | None = None,
        *,
        client=None,
        api_key: str = "",
        api_secret: str = "",
        contract_size_lookup=None,
        qty_step_lookup=None,
        on_close: Callable[[Order], None] | None = None,
        leverage: int = 5,
    ) -> None:
        self.allowed_pairs = allowed_pairs or set()
        self._orders: dict[str, Order] = {}
        if client is not None:
            self._client = client
        else:
            opts: dict = {"enableRateLimit": True}
            if api_key and api_secret:
                opts["apiKey"] = api_key
                opts["secret"] = api_secret
            self._client = ccxt.delta(opts)

        # Pluggable so tests can inject lookups without monkey-patching ccxt.
        # In production these are wired to DeltaExchange instances.
        self._contract_size = contract_size_lookup or self._lookup_contract_size
        self._qty_step = qty_step_lookup or self._lookup_qty_step

        # Audit gap #1: live brokers publish close events so DailyLossLimit
        # and Account see realised R-multiples. Initialised to None; the
        # router auto-wires its `on_close` handler when the attribute
        # exists (see SignalRouter.__init__).
        self._on_close = on_close

        # Audit gap #15 (§K): require 2 consecutive empty fetch_positions
        # reads on the SAME pair before flipping a local Order to FILLED.
        # A single transient empty response (rate-limit, network flap)
        # should not free every cap.
        self._zero_position_streak: dict[str, int] = {}

        # Safety: set per-pair leverage to a known value on construction.
        # Delta's default-leverage is whatever was last set on the product
        # in the UI (could be 50×) — without this, a properly-sized order
        # liquidates instantly if the UI default is high. Failures are
        # logged but don't block construction (testnet / missing keys).
        self._leverage = leverage
        for pair in self.allowed_pairs:
            try:
                self._client.set_leverage(self._leverage, pair)
            except Exception as exc:
                log.warning("set_leverage(%s, %s) failed: %s", self._leverage, pair, exc)

    # ---- gating ----------------------------------------------------------

    def _check_allowed(self, order: Order) -> None:
        if not _settings.enable_live_trading:
            raise LiveTradingDisabled(
                "ENABLE_LIVE_TRADING is False — flip the kill switch in "
                "settings (and only after a full Phase 8.5 review)."
            )
        if order.pair not in self.allowed_pairs:
            raise LiveTradingDisabled(
                f"Pair {order.pair!r} not in allowed_pairs={sorted(self.allowed_pairs)!r}."
            )

    # ---- market metadata (default lookups via ccxt) ----------------------

    def _markets(self) -> dict:
        try:
            return self._client.load_markets()
        except Exception:
            return {}

    def _lookup_contract_size(self, pair: str) -> float:
        m = self._markets().get(pair) or {}
        cs = m.get("contractSize")
        return float(cs) if cs is not None else 1.0

    def _lookup_qty_step(self, pair: str) -> float:
        m = self._markets().get(pair) or {}
        step = (m.get("precision") or {}).get("amount")
        return float(step) if step is not None else 1.0

    # ---- quantization -----------------------------------------------------

    def _to_contracts(self, pair: str, coin_qty: float) -> int:
        """Coin quantity → integer contract count, floored to qty_step.

        Delta perpetuals require integer contracts (qty step = 1.0). We
        floor rather than round so we never exceed the risk envelope the
        router calculated. A floor to zero means "too small to trade" —
        return 0 and let the caller decide whether to refuse.
        """
        contract_size = self._contract_size(pair) or 1.0
        step = self._qty_step(pair) or 1.0
        contracts = coin_qty / contract_size
        floored = math.floor(contracts / step) * step
        return int(floored)

    # ---- core placement ---------------------------------------------------

    def place_order(self, order: Order) -> Order:
        """Place an entry-market + SL-stop + TP-limit bracket on Delta.

        The Order is mutated in place: `qty` is overwritten with the
        venue-native integer contract count, and `entry_order_id` /
        `sl_order_id` / `tp_order_id` are populated.
        """
        self._check_allowed(order)

        contracts = self._to_contracts(order.pair, order.qty)
        if contracts <= 0:
            raise ValueError(
                f"Computed qty for {order.pair} is {contracts} contracts "
                f"(coin qty {order.qty}, contract size "
                f"{self._contract_size(order.pair)}). Order rejected — "
                f"too small to trade."
            )
        # Replace the router's coin-qty with the venue-native contract qty
        # so cancel() / reconciliation use the same number the exchange
        # knows about.
        order.qty = contracts

        opposite = "sell" if order.side == "BUY" else "buy"
        side = order.side.lower()

        # 1) Market entry. Opens the position.
        entry = self._client.create_order(order.pair, "market", side, contracts)
        order.entry_order_id = entry.get("id") if isinstance(entry, dict) else None

        # 2) Reduce-only stop-market SL.
        try:
            sl = self._client.create_order(
                order.pair,
                "stop_market",
                opposite,
                contracts,
                None,
                {"stopPrice": order.sl, "reduceOnly": True},
            )
        except Exception as exc:
            log.error("SL placement failed for %s — emergency flatten: %s", order.pair, exc)
            self._emergency_flatten(order.pair, opposite, contracts)
            raise
        order.sl_order_id = sl.get("id") if isinstance(sl, dict) else None

        # 3) Reduce-only limit TP.
        try:
            tp = self._client.create_order(
                order.pair,
                "limit",
                opposite,
                contracts,
                order.tp,
                {"reduceOnly": True},
            )
        except Exception as exc:
            log.error(
                "TP placement failed for %s — cancelling SL + emergency flatten: %s",
                order.pair,
                exc,
            )
            if order.sl_order_id:
                try:
                    self._client.cancel_order(order.sl_order_id, order.pair)
                except Exception as cancel_exc:
                    log.warning("could not cancel orphaned SL: %s", cancel_exc)
            self._emergency_flatten(order.pair, opposite, contracts)
            raise
        order.tp_order_id = tp.get("id") if isinstance(tp, dict) else None

        order.status = "OPEN"
        self._orders[order.id] = order
        log.info(
            "live placed %s %s qty=%d (contracts) entry_id=%s sl_id=%s tp_id=%s",
            order.side,
            order.pair,
            contracts,
            order.entry_order_id,
            order.sl_order_id,
            order.tp_order_id,
        )
        return order

    def _emergency_flatten(self, pair: str, opposite: str, qty: int) -> None:
        """Best-effort reduce-only market close after a partial-bracket
        failure. Swallows its own errors — the caller's raise must still
        surface so the orchestrator knows placement failed."""
        try:
            self._client.create_order(
                pair,
                "market",
                opposite,
                qty,
                None,
                {"reduceOnly": True},
            )
        except Exception as exc:
            log.critical(
                "EMERGENCY FLATTEN FAILED for %s — manual intervention required: %s",
                pair,
                exc,
            )

    # ---- cancellation -----------------------------------------------------

    def cancel(self, order_id: str) -> bool:
        """Cancel both protective legs and flatten any residual fill."""
        order = self._orders.get(order_id)
        if order is None or not order.is_open():
            return False

        opposite = "sell" if order.side == "BUY" else "buy"
        if order.sl_order_id:
            try:
                self._client.cancel_order(order.sl_order_id, order.pair)
            except Exception as exc:
                log.warning("SL cancel failed for %s: %s", order_id, exc)
        if order.tp_order_id:
            try:
                self._client.cancel_order(order.tp_order_id, order.pair)
            except Exception as exc:
                log.warning("TP cancel failed for %s: %s", order_id, exc)
        try:
            self._client.create_order(
                order.pair,
                "market",
                opposite,
                order.qty,
                None,
                {"reduceOnly": True},
            )
        except Exception as exc:
            log.warning("Flatten market failed for %s: %s", order_id, exc)
            return False

        order.status = "CANCELLED"
        return True

    # ---- introspection ----------------------------------------------------

    def on_bar(self, pair: str, bar: dict) -> list[Order]:
        """Per-iteration reconcile checkpoint (called from the scan loop).
        The exchange — not us — actually simulates fills; we just sync."""
        try:
            self._reconcile_from_exchange()
        except Exception:  # noqa: BLE001
            pass
        return []

    def positions(self) -> list[Order]:
        """Local positions reconciled against Delta's fetch_positions."""
        try:
            self._reconcile_from_exchange()
        except Exception as exc:
            log.warning("position reconcile failed: %s", exc)
        return [o for o in self._orders.values() if o.is_open()]

    def _reconcile_from_exchange(self) -> None:
        """Pull `fetch_positions` and close any local Order whose qty has
        gone to zero. No-op when the allowed set is empty.

        Audit gap #1 follow-up: when transitioning to FILLED, look up the
        actual SL/TP fill price via `fetch_order` so the close event the
        router fires carries a real realised-R, not zero. Failure to look
        up the price falls through to a MANUAL close (close_price = entry,
        R = 0) so caps stay live but the trade is neutralised.

        Audit gap #15 (§K): only flip after two consecutive empty reads
        on the same pair — a single network blip otherwise frees every
        cap. The streak is per-pair and resets when the exchange reports
        the position live again.
        """
        if not self.allowed_pairs:
            return
        positions = self._client.fetch_positions(symbols=sorted(self.allowed_pairs))
        live_pairs = {p.get("symbol") for p in positions if float(p.get("contracts") or 0) > 0}
        for o in self._orders.values():
            if not o.is_open():
                continue
            if o.pair in live_pairs:
                self._zero_position_streak[o.pair] = 0
                continue
            streak = self._zero_position_streak.get(o.pair, 0) + 1
            self._zero_position_streak[o.pair] = streak
            if streak >= 2:
                self._finalize_filled(o)
                self._zero_position_streak[o.pair] = 0

    def _finalize_filled(self, order: Order) -> None:
        """Mark `order` FILLED with a real close_price + reason, then fire
        `_on_close` so the cap layer + account record realised R.

        Resolution order:
          1. SL leg shows status=closed/filled → close_price = SL fill avg
             (or order.sl if avg missing), reason="SL".
          2. TP leg shows status=closed/filled → close_price = TP fill avg,
             reason="TP".
          3. Neither — assume manual / external close → close_price =
             order.entry, reason="MANUAL", realised R = 0.
        """
        order.status = "FILLED"
        order.closed_at = datetime.now(timezone.utc)
        order.close_price = order.entry  # default — overridden below
        order.close_reason = "MANUAL"

        for leg_id, leg_price, leg_reason in (
            (order.sl_order_id, order.sl, "SL"),
            (order.tp_order_id, order.tp, "TP"),
        ):
            if not leg_id:
                continue
            try:
                info = self._client.fetch_order(leg_id, order.pair)
            except Exception as exc:
                log.warning("fetch_order(%s) failed during close: %s", leg_id, exc)
                continue
            status = (info or {}).get("status", "").lower()
            if status in ("closed", "filled"):
                avg = info.get("average") or info.get("price")
                order.close_price = float(avg) if avg is not None else leg_price
                order.close_reason = leg_reason
                break

        if self._on_close is not None:
            try:
                self._on_close(order)
            except Exception:  # noqa: BLE001 — broker state already updated
                log.exception("_on_close callback raised for %s", order.id)

    def on_reconnect(self) -> None:
        """Restore stubs for any pre-existing position on an allowed pair so
        the cap layer knows we already have exposure after a restart."""
        if not self.allowed_pairs:
            return
        try:
            positions = self._client.fetch_positions(symbols=sorted(self.allowed_pairs))
        except Exception as exc:
            log.warning("on_reconnect fetch_positions failed: %s", exc)
            return
        for p in positions:
            qty = float(p.get("contracts") or 0)
            if qty <= 0:
                continue
            pair = p.get("symbol")
            side = "BUY" if (p.get("side") or "").lower() == "long" else "SELL"
            entry_price = float(p.get("entryPrice") or p.get("markPrice") or 0)
            stub = Order(
                pair=pair,
                side=side,
                entry=entry_price,
                sl=entry_price,
                tp=entry_price,
                qty=int(qty),
                status="OPEN",
            )
            self._orders[stub.id] = stub
