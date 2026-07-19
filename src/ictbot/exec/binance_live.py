"""
BinanceLiveBroker — places REAL orders on Binance USDT-M Futures.

GATED by:
  1. `settings.enable_live_trading` must be True.
  2. The pair must be in `allowed_pairs`.

Otherwise `place_order` raises `LiveTradingDisabled`.

Bracket placement (same 3-leg shape as DeltaLiveBroker):
  1. market entry on `order.side`
  2. reduce-only stop-market at `order.sl`   (type=STOP_MARKET, stopPrice=sl)
  3. reduce-only limit at `order.tp`         (type=LIMIT, reduceOnly=true)

Audit gap #5 (bracket rollback) honoured: any failure on leg 2 or 3
fires an emergency reduce-only market flatten + re-raises. The Order
is registered in `self._orders` only when all three legs succeed.

Why Binance is the primary testing venue (Delta is the mainnet target):
  - Binance Futures testnet (testnet.binancefuture.com) has no KYC
    barrier and a generous USDT faucet — ideal for end-to-end
    validation without spending real money.

Quantization:
  Binance USDT-M perps take coin quantities directly (BTC qty, not
  contracts), with a `precision.amount` step per pair. The router
  already floors to the exchange's step via `SignalRouter._lookup_qty_step`,
  so we don't need a contract-size conversion like DeltaLiveBroker.

Testnet routing:
  `ccxt.binance.set_sandbox_mode(True)` rewrites the URLs to
  `testnet.binancefuture.com`.

Tests live in tests/test_binance_live_broker.py. Every ccxt interaction
is mocked — never touch the real exchange from tests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

import ccxt

from ictbot.exec.orders import Order
from ictbot.settings import settings as _settings

log = logging.getLogger(__name__)


class LiveTradingDisabled(RuntimeError):
    """Raised when live trading is requested but the kill switch is off."""


class BinanceLiveBroker:
    name = "binance-live"

    def __init__(
        self,
        allowed_pairs: set[str] | None = None,
        *,
        client=None,
        testnet: bool = False,
        leverage: int = 5,
        api_key: str = "",
        api_secret: str = "",
        on_close: Callable[[Order], None] | None = None,
    ) -> None:
        self.allowed_pairs = allowed_pairs or set()
        self._orders: dict[str, Order] = {}
        # Audit gap #1: live brokers publish close events so DailyLossLimit
        # and Account see realised R-multiples. The router auto-wires its
        # `on_close` handler when the attribute exists.
        self._on_close = on_close
        # Audit gap #15: require 2 consecutive empty `fetch_positions` reads
        # on the SAME pair before flipping a local Order to FILLED.
        self._zero_position_streak: dict[str, int] = {}
        # Binance Futures-specific ccxt opts:
        #   defaultType=future  → USDT-M perps endpoints
        #   newClientOrderId    → server will accept None and stamp its own
        # We do NOT pass dualSidePosition / positionSide — one-way mode is
        # the default and what the router assumes (no per-leg side flag).
        self._perp_params: dict = {}
        self._leverage = leverage

        if client is not None:
            self._client = client
        else:
            opts: dict = {
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
            if api_key:
                opts["apiKey"] = api_key
            if api_secret:
                opts["secret"] = api_secret
            self._client = ccxt.binance(opts)
            if testnet:
                self._apply_testnet_routing(self._client)

        # Fix 9.C (plan: Phase 9 per-token completeness): set margin
        # mode + leverage per pair AND read both back to confirm they
        # took effect. The pre-fix path silently logged a warning if
        # set_leverage failed and never set margin mode at all — both
        # leave the broker willing to place orders against an unknown
        # leverage / margin regime.
        for pair in sorted(self.allowed_pairs):
            try:
                self._ensure_pair_init(pair)
            except LiveTradingDisabled:
                # Strict mode: re-raise so the broker refuses to construct.
                raise
            except Exception as exc:
                log.warning("pair init for %s failed (non-strict): %s", pair, exc)

    # ---- per-pair init (Fix 9.C) ----------------------------------------

    def _ensure_pair_init(self, pair: str) -> None:
        """Set margin mode + leverage for `pair` and verify both took.

        Order matters: margin mode must be set before set_leverage on
        some pairs (Binance rejects leverage changes against a wrong
        mode). MarginModeAlreadySet / "no change" errors are swallowed.

        Read-back: `fetch_positions(symbols=[pair])` returns each pair's
        active leverage and marginType. If either drifts from what we
        just requested, raise LiveTradingDisabled in strict mode so the
        scanner refuses to start with mismatched broker state. The
        STRICT_PAIR_INIT env var (default True) gates the strictness so
        operators can fall back to log-and-continue for pairs that
        legitimately can't be normalized (rare; document the override
        path in ops runbook).
        """
        strict = bool(getattr(_settings, "strict_pair_init", True))

        # 1) margin mode — ISOLATED is the convention the risk math
        #    assumes. Cross can silently let one pair's losses eat
        #    another's margin.
        margin_locked_by_position = False
        try:
            self._client.set_margin_mode("ISOLATED", pair)
        except Exception as exc:
            msg = str(exc).lower()
            # ccxt raises MarginModeAlreadySet OR Binance returns
            # "no need to change margin type" / -4046. Swallow either.
            if "already" in msg or "no need" in msg or "-4046" in msg:
                pass
            elif (
                "-4047" in msg
                or "open orders" in msg
                or "open position" in msg
                or "cannot be changed" in msg
            ):
                # Binance -4047 — margin mode can't be changed while a
                # position or order is open. Defer the strict check
                # (caller flips margin mode AFTER the position closes
                # on the next restart). Verifying still happens, but the
                # mismatch becomes a warning rather than a refusal.
                margin_locked_by_position = True
                log.warning(
                    "set_margin_mode(ISOLATED, %s) deferred — pair has an open position/order: %s",
                    pair,
                    exc,
                )
            else:
                log.warning("set_margin_mode(ISOLATED, %s) failed: %s", pair, exc)

        # 2) leverage. Same swallow-on-already-set semantics.
        leverage_locked_by_position = False
        try:
            self._client.set_leverage(self._leverage, pair)
        except Exception as exc:
            msg = str(exc).lower()
            if "no need" in msg or "leverage not modified" in msg:
                pass
            elif "-4048" in msg or "open orders" in msg or "open position" in msg:
                # Same shape as -4047 but for leverage. Defer strict.
                leverage_locked_by_position = True
                log.warning(
                    "set_leverage(%s, %s) deferred — pair has an open position/order: %s",
                    self._leverage,
                    pair,
                    exc,
                )
            else:
                log.warning("set_leverage(%s, %s) failed: %s", self._leverage, pair, exc)

        # 3) read-back. fetch_positions returns one row per pair (even
        # when flat) and ccxt normalizes the leverage + marginMode keys.
        if not strict:
            return
        try:
            rows = self._client.fetch_positions(symbols=[pair])
            rows_list = list(rows) if rows is not None else []
        except TypeError:
            # Mocked client returned a non-iterable. Skip verification
            # silently — production ccxt always returns a list.
            return
        except Exception as exc:
            # Testnet sometimes hiccups on the first call after
            # set_leverage; treat as best-effort even in strict mode
            # (otherwise we'd refuse to start over a transient network
            # blip). Log loudly so ops can correlate with the warning.
            log.warning(
                "fetch_positions read-back failed for %s (strict mode skipping verification): %s",
                pair,
                exc,
            )
            return

        for row in rows_list:
            if not isinstance(row, dict) or row.get("symbol") != pair:
                continue
            actual_lev = row.get("leverage")
            if actual_lev is not None and not leverage_locked_by_position:
                try:
                    if int(float(actual_lev)) != int(self._leverage):
                        raise LiveTradingDisabled(
                            f"leverage mismatch on {pair}: requested "
                            f"{self._leverage}, exchange reports {actual_lev}. "
                            "Refusing to start; set STRICT_PAIR_INIT=false to "
                            "override (not recommended)."
                        )
                except (TypeError, ValueError):
                    pass
            info = row.get("info") or {}
            margin_type = (row.get("marginMode") or info.get("marginType") or "").lower()
            if margin_type and margin_type != "isolated" and not margin_locked_by_position:
                raise LiveTradingDisabled(
                    f"margin mode mismatch on {pair}: requested ISOLATED, "
                    f"exchange reports {margin_type!r}. Refusing to start; "
                    "set STRICT_PAIR_INIT=false to override."
                )
            break

    @staticmethod
    def _apply_testnet_routing(client) -> None:
        """Route a Binance ccxt client at testnet.binancefuture.com.

        Two fixes ship together because they're inseparable: testnet
        access requires BOTH the fapi URL override AND a short-circuit
        for ccxt's hidden SAPI pre-flights.

        1) ccxt's set_sandbox_mode(True) raises NotSupported on Binance
           Futures (deprecated 2024). But the testnet URLs are still
           alive at testnet.binancefuture.com and ccxt still ships them
           under client.urls["test"]. So we manually copy the fapi test
           URLs over the regular ones — bypasses the deprecation guard.

        2) Several ccxt high-level methods (fetch_balance, set_leverage,
           fetch_positions, create_order) do an upfront SAPI probe to
           `api.binance.com/sapi/v1/capital/config/getall` to load
           coin/currency metadata. Binance has NO testnet SAPI host, so
           every probe 401s with code -2008 ("Invalid Api-Key ID") and
           the high-level wrapper raises before the actual fapi call.
           Patching `client.fetch` to return an empty list for any SAPI
           URL satisfies the pre-flight without a network call, after
           which the real fapi request runs cleanly.

        Verified 2026-06-04 against a real demo-mode key.
        """
        test_urls = client.urls.get("test") or {}
        for k in (
            "fapiPublic",
            "fapiPrivate",
            "fapiPublicV2",
            "fapiPrivateV2",
            "fapiPublicV3",
            "fapiPrivateV3",
        ):
            if k in test_urls:
                client.urls["api"][k] = test_urls[k]

        original_fetch = client.fetch

        def patched_fetch(url, method="GET", headers=None, body=None):
            # Short-circuit any SAPI URL — no testnet host exists and
            # the data isn't needed for futures execution.
            if "/sapi/" in url:
                return []
            return original_fetch(url, method, headers, body)

        client.fetch = patched_fetch
        log.info(
            "Binance live broker routed to testnet futures "
            "(testnet.binancefuture.com / demo.binance.com keys); SAPI calls short-circuited."
        )

    # ---- gating ----------------------------------------------------------

    def _check_allowed(self, order: Order) -> None:
        if not _settings.enable_live_trading:
            raise LiveTradingDisabled(
                "ENABLE_LIVE_TRADING is False — flip the kill switch in "
                "settings before placing live orders."
            )
        if order.pair not in self.allowed_pairs:
            raise LiveTradingDisabled(
                f"Pair {order.pair!r} not in allowed_pairs={sorted(self.allowed_pairs)!r}."
            )

    # ---- precision normalization (Fix 9.D) -------------------------------

    @staticmethod
    def _coerce_numeric(raw, fallback: float) -> float:
        """Coerce a value coming back from ccxt to a clean float.

        Real ccxt returns a string (e.g. `"95.000"`); some adapters
        return a float directly. Tests inject MagicMock clients whose
        `amount_to_precision` / `price_to_precision` return MagicMock
        instances — `float(MagicMock())` is 1.0 by default, which would
        silently corrupt `order.sl` / `order.tp`. Guard by requiring
        the raw value to be a recognised numeric type before float()."""
        if not isinstance(raw, (int, float, str)):
            return fallback
        try:
            return float(raw)
        except (TypeError, ValueError):
            return fallback

    def _amount_to_precision(self, pair: str, qty) -> float:
        """Round `qty` to the pair's step using ccxt's official helper.

        Pre-Fix-9.D, raw floats reached Binance. Different pairs have
        different precision:
          BTC: 3 decimals (0.001)
          ETH: 3 decimals (0.001)
          SOL: 0 decimals (1) — must be whole units on testnet
          XRP: 1 decimal (0.1)
          PAXG: 4 decimals (0.0001)
        Mismatches got either rejected or silently rounded by Binance,
        which broke realised-R because the rounded qty no longer matched
        the risk math. Falls back to the raw value if the helper isn't
        available (paper-broker / older ccxt).
        """
        fallback = float(qty)
        try:
            raw = self._client.amount_to_precision(pair, qty)
        except Exception as exc:
            log.warning("amount_to_precision(%s, %s) failed: %s", pair, qty, exc)
            return fallback
        return self._coerce_numeric(raw, fallback)

    def _price_to_precision(self, pair: str, price) -> float:
        """Same shape as `_amount_to_precision` for price-typed fields
        (stopPrice on STOP_MARKET, limit price on TP).

        XRP tick = 0.0001, PAXG tick = 0.01, BTC tick = 0.10. Unrounded
        prices on STOP_MARKET get silently rounded by Binance, drifting
        the trigger off the intended SL (Binance algo-queue gotcha)."""
        fallback = float(price)
        try:
            raw = self._client.price_to_precision(pair, price)
        except Exception as exc:
            log.warning("price_to_precision(%s, %s) failed: %s", pair, price, exc)
            return fallback
        return self._coerce_numeric(raw, fallback)

    # ---- core placement --------------------------------------------------

    def place_order(self, order: Order) -> Order:
        """Place an entry-market + SL-stop + TP-limit bracket on Binance.

        Audit gap #5: any failure on SL or TP triggers an emergency
        reduce-only market flatten + re-raise. The Order is registered
        in self._orders only when all three legs succeed.

        ccxt.PermissionDenied (e.g. account-level restriction, KYC,
        sub-account perms) is re-raised as `LiveTradingDisabled` so
        the scanner logs a clean one-liner instead of a long traceback
        every cycle on the same recurring signal.
        """
        self._check_allowed(order)

        opposite = "sell" if order.side == "BUY" else "buy"
        side = order.side.lower()

        # Fix 9.D (plan: Phase 9): normalize qty + SL/TP to the pair's
        # precision BEFORE the first ccxt call. Pre-fix path passed raw
        # floats which Binance silently rounded — the rounded values
        # then mismatched the journal-stored entry/sl/tp, breaking
        # realised R for any pair whose precision differed from ours.
        # Stamp the normalized values back onto the Order so downstream
        # (journal, on_close, realised_pnl_R) sees the same numbers
        # Binance is about to act on.
        order.qty = self._amount_to_precision(order.pair, order.qty)
        order.sl = self._price_to_precision(order.pair, order.sl)
        order.tp = self._price_to_precision(order.pair, order.tp)

        # 1) Market entry. This is the fill that opens the position.
        try:
            entry = self._client.create_order(
                order.pair, "market", side, order.qty, None, {**self._perp_params}
            )
        except ccxt.PermissionDenied as exc:
            raise LiveTradingDisabled(
                f"Binance refused to place order on {order.pair}: {exc}. "
                "Likely cause: account-level restriction (KYC required, "
                "API key missing futures permission, or geographic "
                "block). Fix on the testnet or mainnet account settings."
            ) from exc
        order.entry_order_id = entry.get("id") if isinstance(entry, dict) else None

        # Fix 2.E (plan: live P&L clean-up): capture the ACTUAL fill
        # price + apply slippage handling BEFORE we place SL/TP. The
        # pre-fix path stored only the order id and left order.entry at
        # the strategy's pre-bar-close price, so any market slippage
        # silently shrank the effective stop distance (Phase 1
        # diagnostic root cause #1).
        strategy_entry = float(order.entry)
        actual_avg = self._resolve_fill_price(entry, order)
        if actual_avg is not None and actual_avg > 0 and strategy_entry > 0:
            slip_bps = (actual_avg - strategy_entry) / strategy_entry * 10_000.0
            # Sign convention: positive slip is "filled higher than the
            # strategy expected" — bad for BUY (paid up), good for SELL
            # (sold higher). Compare the *unfavourable* slip against the
            # ceiling rather than abs(), so a wildly favourable fill
            # never gets rejected.
            unfavourable_bps = slip_bps if order.side == "BUY" else -slip_bps
            if unfavourable_bps > _settings.max_entry_slippage_bps:
                log.warning(
                    "entry slip %.1f bps exceeds MAX_ENTRY_SLIPPAGE_BPS (%.1f) "
                    "on %s %s — emergency-flatten",
                    unfavourable_bps,
                    _settings.max_entry_slippage_bps,
                    order.pair,
                    order.side,
                )
                self._emergency_flatten(order.pair, opposite, order.qty)
                raise LiveTradingDisabled(
                    f"entry slippage {unfavourable_bps:.1f} bps > "
                    f"MAX_ENTRY_SLIPPAGE_BPS ({_settings.max_entry_slippage_bps:.1f}) "
                    f"on {order.pair} {order.side}; flattened to avoid holding a bad fill"
                )
            order.entry = actual_avg
            order.filled_at = datetime.now(timezone.utc)
            if _settings.re_anchor_bracket:
                # Preserve the intended risk distance — shift both legs
                # by the same dollar offset as the entry fill drift,
                # then re-normalize to the pair's tick (Fix 9.D — the
                # drift add can re-introduce sub-tick precision).
                drift = actual_avg - strategy_entry
                order.sl = self._price_to_precision(order.pair, float(order.sl) + drift)
                order.tp = self._price_to_precision(order.pair, float(order.tp) + drift)

        # 2) Stop-market SL with reduceOnly so it can only close the
        #    position, never flip it. Binance uses "STOP_MARKET" type
        #    with `stopPrice` param.
        try:
            sl = self._client.create_order(
                order.pair,
                "stop_market",
                opposite,
                order.qty,
                None,
                {**self._perp_params, "stopPrice": order.sl, "reduceOnly": True},
            )
        except Exception as exc:
            log.error("SL placement failed for %s — emergency flatten: %s", order.pair, exc)
            self._emergency_flatten(order.pair, opposite, order.qty)
            raise
        order.sl_order_id = sl.get("id") if isinstance(sl, dict) else None

        # 3) Limit TP, reduceOnly.
        try:
            tp = self._client.create_order(
                order.pair,
                "limit",
                opposite,
                order.qty,
                order.tp,
                {**self._perp_params, "reduceOnly": True},
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
            self._emergency_flatten(order.pair, opposite, order.qty)
            raise
        order.tp_order_id = tp.get("id") if isinstance(tp, dict) else None

        order.status = "OPEN"
        self._orders[order.id] = order
        log.info(
            "live placed %s %s qty=%s entry_id=%s sl_id=%s tp_id=%s",
            order.side,
            order.pair,
            order.qty,
            order.entry_order_id,
            order.sl_order_id,
            order.tp_order_id,
        )
        return order

    def _resolve_fill_price(self, entry_resp: dict | None, order: Order) -> float | None:
        """Pull the entry order's actual filled average from `entry_resp`,
        falling back to `fetch_order` when ccxt didn't populate the
        synchronous response. Returns None when no price can be resolved
        — caller treats that as "fall through to legacy behaviour" so a
        broker that returns sparse market-order responses (testnet
        sometimes does) doesn't break placement.
        """
        if isinstance(entry_resp, dict):
            for key in ("average", "price"):
                v = entry_resp.get(key)
                if v is not None:
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if fv > 0:
                        return fv
        oid = order.entry_order_id
        if not oid:
            return None
        try:
            info = self._client.fetch_order(oid, order.pair)
        except Exception as exc:
            log.warning("fetch_order(%s) failed during fill-price resolve: %s", oid, exc)
            return None
        if not isinstance(info, dict):
            return None
        for key in ("average", "price"):
            v = info.get(key)
            if v is not None:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if fv > 0:
                    return fv
        return None

    def _emergency_flatten(self, pair: str, opposite: str, qty: float) -> None:
        """Best-effort reduce-only market close after a partial bracket
        failure. Swallows its own errors — the caller's raise must
        still surface so the orchestrator knows placement failed.

        Fix 5.D (plan: Phase 5 Tier 2): on failure, fire a TG critical
        alert so the operator sees the manual-intervention signal in
        real time, not buried in a log file. The TG send itself is
        also try/except'd — a notification failure must NEVER mask the
        original critical condition.
        """
        try:
            self._client.create_order(
                pair,
                "market",
                opposite,
                qty,
                None,
                {**self._perp_params, "reduceOnly": True},
            )
        except Exception as exc:
            log.critical(
                "EMERGENCY FLATTEN FAILED for %s — manual intervention required: %s",
                pair,
                exc,
            )
            try:
                from ictbot.notify.telegram import send_telegram

                send_telegram(
                    f"[BOT EMERGENCY] flatten failed pair={pair} "
                    f"qty={qty} side={opposite} err={exc}\n"
                    f"Position may be unhedged — manual intervention required."
                )
            except Exception as tg_exc:  # noqa: BLE001
                log.error("TG emergency alert also failed: %s", tg_exc)

    # ---- cancellation -----------------------------------------------------

    def cancel(self, order_id: str) -> bool:
        """Cancel both protective legs + flatten any residual fill."""
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
                {**self._perp_params, "reduceOnly": True},
            )
        except Exception as exc:
            log.warning("Flatten market failed for %s: %s", order_id, exc)
            return False

        order.status = "CANCELLED"
        return True

    # ---- introspection ----------------------------------------------------

    def on_bar(self, pair: str, bar: dict) -> list[Order]:
        """Per-iteration reconcile checkpoint, called from the scan
        loop. Returns the orders this bar closed."""
        closed_before = {oid for oid, o in self._orders.items() if not o.is_open()}
        try:
            self._reconcile_from_exchange()
        except Exception as exc:  # noqa: BLE001
            log.warning("position reconcile failed during on_bar: %s", exc)
            return []
        return [
            o for oid, o in self._orders.items() if not o.is_open() and oid not in closed_before
        ]

    def positions(self) -> list[Order]:
        """Local positions reconciled against Binance's fetch_positions."""
        try:
            self._reconcile_from_exchange()
        except Exception as exc:
            log.warning("position reconcile failed: %s", exc)
        return [o for o in self._orders.values() if o.is_open()]

    def equity(self) -> float:
        """USDT-M Futures wallet balance (the router's risk sizer reads this).

        Works on testnet too — the SAPI short-circuit applied in
        `_apply_testnet_routing` makes ccxt's `fetch_balance` skip the
        currency-config pre-flight that has no testnet host.
        """
        try:
            bal = self._client.fetch_balance()
            usdt = bal.get("USDT") or {}
            return float(usdt.get("free") or 0.0)
        except Exception as exc:
            log.warning("equity() failed: %s", exc)
            return 0.0

    def qty_step(self, pair: str) -> float:
        """Per-pair LOT_SIZE step — Binance rejects qty that's not a
        multiple of `precision.amount`."""
        try:
            m = self._client.load_markets().get(pair) or {}
            step = (m.get("precision") or {}).get("amount")
            return float(step) if step is not None else 0.001
        except Exception:
            return 0.001

    def min_notional(self, pair: str) -> float:
        try:
            m = self._client.load_markets().get(pair) or {}
            limits = m.get("limits") or {}
            cost = limits.get("cost") or {}
            mn = cost.get("min")
            return float(mn) if mn is not None else 0.0
        except Exception:
            return 0.0

    # ---- Fix 9.E (Phase 9): boot-time per-pair readiness check -----------

    def verify_pair_readiness(self, pair: str) -> dict:
        """Verify everything needed to place a real order on `pair`.

        Returns a status dict (never raises — caller decides whether to
        refuse boot based on the result):
          leverage: int | None — exchange-reported leverage
          margin_mode: str | None — "isolated" / "cross" / None
          ticker_price: float | None — last close, used for sizing math
          min_notional: float — exchange's min cost in USDT
          sized_qty: float | None — qty derived from current equity ×
            risk_pct / (price × sl_frac). None when any input missing.
          sized_notional: float | None — sized_qty × ticker_price.
          ok: bool — True iff all critical checks passed.
          reasons: list[str] — human-readable rejection causes when not ok.

        The math intentionally uses the per-pair `sl_frac` (Fix 9.A) so
        boot-time checks reflect the actual sizing the strategy will
        use — a pair with a tight SL fraction may pass min_notional
        while a pair with a wide SL won't.
        """
        status: dict = {
            "leverage": None,
            "margin_mode": None,
            "ticker_price": None,
            "min_notional": 0.0,
            "sized_qty": None,
            "sized_notional": None,
            "ok": True,
            "reasons": [],
        }

        # 1) Leverage + margin mode from a fresh fetch_positions.
        try:
            rows = self._client.fetch_positions(symbols=[pair]) or []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict) or row.get("symbol") != pair:
                    continue
                lev = row.get("leverage")
                if lev is not None:
                    try:
                        status["leverage"] = int(float(lev))
                    except (TypeError, ValueError):
                        pass
                info = row.get("info") or {}
                mm = row.get("marginMode") or info.get("marginType")
                if mm:
                    status["margin_mode"] = str(mm).lower()
                break
        except Exception as exc:
            log.warning("verify_pair_readiness(%s) fetch_positions: %s", pair, exc)

        if status["leverage"] is not None and status["leverage"] != self._leverage:
            status["ok"] = False
            status["reasons"].append(f"leverage={status['leverage']} (want {self._leverage})")
        if status["margin_mode"] and status["margin_mode"] != "isolated":
            status["ok"] = False
            status["reasons"].append(f"margin_mode={status['margin_mode']!r} (want 'isolated')")

        # 2) Ticker — needed for the sizing math AND a basic liveness check.
        try:
            tk = self._client.fetch_ticker(pair) or {}
            last = tk.get("last") if isinstance(tk, dict) else None
            if isinstance(last, (int, float, str)):
                try:
                    status["ticker_price"] = float(last)
                except (TypeError, ValueError):
                    pass
        except Exception as exc:
            log.warning("verify_pair_readiness(%s) fetch_ticker: %s", pair, exc)

        if not status["ticker_price"] or status["ticker_price"] <= 0:
            status["ok"] = False
            status["reasons"].append("no ticker price")

        # 3) Min notional + hypothetical risk-sized notional.
        try:
            status["min_notional"] = self.min_notional(pair)
        except Exception as exc:
            log.warning("verify_pair_readiness(%s) min_notional: %s", pair, exc)

        try:
            equity = self.equity()
        except Exception:
            equity = 0.0

        sl_frac = float(_settings.get_sl_frac(pair))
        risk_pct = float(_settings.risk_pct_live)
        px = status["ticker_price"] or 0.0
        if equity > 0 and px > 0 and sl_frac > 0 and risk_pct > 0:
            sized_qty = (equity * risk_pct) / (px * sl_frac)
            try:
                sized_qty = self._amount_to_precision(pair, sized_qty)
            except Exception:
                pass
            status["sized_qty"] = sized_qty
            status["sized_notional"] = sized_qty * px

        if (
            status["sized_notional"] is not None
            and status["min_notional"]
            and status["sized_notional"] < status["min_notional"]
        ):
            status["ok"] = False
            status["reasons"].append(
                f"sized_notional=${status['sized_notional']:.2f} < "
                f"min_notional=${status['min_notional']:.2f}"
            )

        return status

    def verify_all_pairs_ready(self) -> dict[str, dict]:
        """Verify every allowed pair. Returns {pair: status_dict}.

        The scanner's `_build_router` calls this after `on_reconnect` and
        refuses to start (when STRICT_PAIR_INIT=true) if any pair's
        `ok` is False. Banner output handled by the caller.
        """
        return {pair: self.verify_pair_readiness(pair) for pair in sorted(self.allowed_pairs)}

    def _reconcile_from_exchange(self) -> None:
        """Pull `fetch_positions` and close any local Order whose qty is
        zero. No-op when the allowed set is empty.

        Audit gap #15: only flip after two consecutive empty reads on the
        same pair — a single network blip otherwise frees every cap.
        """
        if not self.allowed_pairs:
            return
        positions = self._client.fetch_positions(symbols=sorted(self.allowed_pairs))
        live_pairs = {
            p.get("symbol")
            for p in positions
            if abs(float(p.get("contracts") or p.get("info", {}).get("positionAmt") or 0)) > 0
        }
        for o in list(self._orders.values()):
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
        """Mark `order` FILLED with a real close_price + reason, then
        fire `_on_close` so the cap layer + account record realised R.

        Fix 5.A (plan: Phase 5 — close known gaps): the pre-fix
        resolution iterated `fetch_order(leg_id, pair)` for the SL/TP
        legs. STOP_MARKET orders on Binance USDT-M live in the
        conditional/algo queue with 16-digit algoIds; `fetch_order`
        queries the regular endpoint and returns -2013 "Order does
        not exist" for them. Every SL fire fell through to MANUAL +
        close_price=entry + pnl_r=0 — the bug that explained the
        anomalous "BE" rows in the archived journal.

        New resolution order:
          1. `fetch_my_trades(pair, since=order.created_at_ms)` to
             find the actual close fill (most recent reduceOnly trade
             after entry). Real fill price, real fee cost.
          2. Infer close_reason by comparing the close fill direction
             to entry — for SELL, close above entry = SL hit; for BUY,
             close below entry = SL hit.
          3. Fall back to the legacy `fetch_order` loop for the
             non-algo (limit TP) case so we don't lose existing
             coverage.
          4. Fall back to MANUAL only when neither path resolves.

        Still sums entry + close-leg fees into `order.fees_paid` per
        Fix 2.F semantics.
        """
        order.status = "FILLED"
        order.closed_at = datetime.now(timezone.utc)
        order.close_price = order.entry
        order.close_reason = "MANUAL"

        entry_fee = self._fee_from_order_id(order.entry_order_id, order.pair)
        close_fee = 0.0

        # Path 1: fetch_my_trades — the algo-queue-safe approach.
        trade_close_fee = self._resolve_close_via_trades(order)
        if trade_close_fee is not None:
            close_fee = trade_close_fee
        else:
            # Path 2 (legacy): fetch_order on the SL/TP legs.
            # Works for limit TPs (regular orders queue) and shouldn't
            # find anything for algo SLs.
            for leg_id, leg_price, leg_reason in (
                (order.sl_order_id, order.sl, "SL"),
                (order.tp_order_id, order.tp, "TP"),
            ):
                if not leg_id:
                    continue
                try:
                    info = self._client.fetch_order(leg_id, order.pair)
                except Exception as exc:  # noqa: BLE001
                    log.debug("fetch_order(%s) skipped during close: %s", leg_id, exc)
                    continue
                status = (info or {}).get("status", "").lower()
                if status in ("closed", "filled"):
                    avg = info.get("average") or info.get("price")
                    order.close_price = float(avg) if avg is not None else leg_price
                    order.close_reason = leg_reason
                    close_fee = self._fee_from_info(info)
                    break

        if entry_fee or close_fee:
            order.fees_paid = float(entry_fee + close_fee)

        if self._on_close is not None:
            try:
                self._on_close(order)
            except Exception:  # noqa: BLE001 — broker state already updated
                log.exception("_on_close callback raised for %s", order.id)

    def _resolve_close_via_trades(self, order: Order) -> float | None:
        """Fix 5.A path 1: query `fetch_my_trades` for reduceOnly
        trades on the pair after entry, infer close fill + reason from
        the most recent one. On success: mutates `order.close_price`
        + `order.close_reason` AND returns the close-trade fee (or
        0.0 if not present). On failure: returns None and leaves the
        order untouched.

        Direction-based reason inference: for a SELL, a close ABOVE
        entry means the SL trigger fired (price moved against us);
        for a BUY, a close BELOW entry means SL. The opposite means
        TP. We don't trust bit-for-bit equality with `order.sl/tp`
        since the actual fill drifts off the trigger by spread.
        """
        try:
            since_ms = (
                int(order.created_at.timestamp() * 1000) if order.created_at is not None else None
            )
            trades = self._client.fetch_my_trades(order.pair, since=since_ms)
        except Exception as exc:  # noqa: BLE001
            log.debug("fetch_my_trades(%s) skipped during close: %s", order.pair, exc)
            return None
        if not trades:
            return None
        # Filter to closing trades on the opposite side.
        # Primary signal: reduceOnly flag in the trade record.
        # Fallback (Fix 6.A): some ccxt code paths — notably manual
        # reduce-only orders sent outside the broker's place_order
        # flow — leave the trade record's reduceOnly flag empty even
        # though the underlying order WAS reduceOnly. In those cases
        # Binance still populates `info.realizedPnl` (or `info.realisedPnl`)
        # with a non-zero value because the trade actually reduced the
        # position. We treat either signal as a valid close marker.
        opposite = "sell" if order.side == "BUY" else "buy"
        closing = []
        for t in trades:
            side = (t.get("side") or "").lower()
            if side != opposite:
                continue
            info = t.get("info") or {}
            ro = t.get("reduceOnly")
            if ro is None:
                ro = info.get("reduceOnly")
            is_reduce_only = str(ro).lower() in ("true", "1")
            # Realized PnL is non-zero iff the trade closed (or
            # partially closed) an existing position. Entry trades
            # have realizedPnl == 0.
            realized = info.get("realizedPnl") or info.get("realisedPnl")
            has_realized = False
            try:
                has_realized = realized is not None and float(realized) != 0.0
            except (TypeError, ValueError):
                has_realized = False
            if not (is_reduce_only or has_realized):
                continue
            closing.append(t)
        if not closing:
            return None
        # Most recent reduceOnly close trade.
        closing.sort(key=lambda t: t.get("timestamp") or 0)
        last = closing[-1]
        price = last.get("price")
        if price is None:
            return None
        try:
            close_price = float(price)
        except (TypeError, ValueError):
            return None
        order.close_price = close_price
        if order.side == "SELL":
            order.close_reason = "SL" if close_price > order.entry else "TP"
        else:
            order.close_reason = "SL" if close_price < order.entry else "TP"
        close_fee = 0.0
        try:
            cost = (last.get("fee") or {}).get("cost")
            if cost is not None:
                close_fee = abs(float(cost))
        except (TypeError, ValueError):
            close_fee = 0.0
        return close_fee

    @staticmethod
    def _fee_from_info(info: dict | None) -> float:
        """Extract the absolute fee cost from a ccxt order dict.
        ccxt unifies Binance's `commission` into `info["fee"]["cost"]`
        (sometimes `info["fees"]` plural list). Returns 0.0 on any
        parse failure so a missing fee never blocks close finalization."""
        if not isinstance(info, dict):
            return 0.0
        fee = info.get("fee")
        if isinstance(fee, dict):
            cost = fee.get("cost")
            try:
                return abs(float(cost)) if cost is not None else 0.0
            except (TypeError, ValueError):
                return 0.0
        fees = info.get("fees")
        if isinstance(fees, list):
            total = 0.0
            for f in fees:
                if isinstance(f, dict):
                    c = f.get("cost")
                    try:
                        total += abs(float(c)) if c is not None else 0.0
                    except (TypeError, ValueError):
                        continue
            return total
        return 0.0

    def _fee_from_order_id(self, oid: str | None, pair: str) -> float:
        """Look up an order's fee via fetch_order. Returns 0.0 on any
        failure mode — fee accounting must never crash close handling."""
        if not oid:
            return 0.0
        try:
            info = self._client.fetch_order(oid, pair)
        except Exception as exc:
            log.warning("fetch_order(%s) failed during fee resolve: %s", oid, exc)
            return 0.0
        return self._fee_from_info(info)

    def on_reconnect(self) -> None:
        """Restore stubs for any pre-existing position on an allowed pair
        so the cap layer knows about exposure surviving a restart.

        Also rebuilds `sl_order_id` / `tp_order_id` by scanning open
        conditional orders on Binance — without this, a restart would
        leave the stubs unable to cancel/flatten their own brackets.

        Fix 9.C: also re-runs `_ensure_pair_init(pair)` so a restart
        re-asserts margin mode + leverage. A stale state survived if a
        previous process modified the pair via the Binance UI between
        restarts.
        """
        if not self.allowed_pairs:
            return
        # Fix 9.C: re-assert margin + leverage. Best-effort across all
        # pairs — one bad pair shouldn't block recovery for the others.
        # Strict mode still raises so the scanner refuses to start with
        # mismatched state.
        for pair in sorted(self.allowed_pairs):
            try:
                self._ensure_pair_init(pair)
            except LiveTradingDisabled:
                raise
            except Exception as exc:
                log.warning(
                    "on_reconnect pair init for %s failed (non-strict): %s",
                    pair,
                    exc,
                )
        try:
            positions = self._client.fetch_positions(symbols=sorted(self.allowed_pairs))
        except Exception as exc:
            log.warning("on_reconnect fetch_positions failed: %s", exc)
            return

        open_orders_by_pair: dict[str, list[dict]] = {}
        try:
            open_orders = self._client.fetch_open_orders()
            for o in open_orders or []:
                open_orders_by_pair.setdefault(o.get("symbol"), []).append(o)
        except Exception as exc:
            log.warning("on_reconnect fetch_open_orders failed: %s", exc)

        for p in positions:
            # Binance's `contracts` may be 0 even when positionAmt is set
            # (depends on ccxt version). Fall back to info.positionAmt.
            contracts = float(p.get("contracts") or 0)
            if contracts == 0:
                try:
                    contracts = abs(float((p.get("info") or {}).get("positionAmt") or 0))
                except Exception:
                    contracts = 0
            if contracts <= 0:
                continue
            pair = p.get("symbol")
            side = "BUY" if (p.get("side") or "").lower() == "long" else "SELL"
            entry_price = float(p.get("entryPrice") or p.get("markPrice") or 0)
            # Fix 5.B (plan: Phase 5 — close known gaps): recover the
            # actual sl/tp prices from open orders so the rebuilt stub
            # has a non-zero risk distance. Without this, realised_pnl_R
            # divides by zero on any close after restart and books +0R
            # regardless of outcome.
            recovered_sl: float | None = None
            recovered_tp: float | None = None
            sl_id: str | None = None
            tp_id: str | None = None
            for oo in open_orders_by_pair.get(pair, []):
                t = (oo.get("type") or "").lower()
                info = oo.get("info") or {}
                ro = oo.get("reduceOnly") or info.get("reduceOnly")
                if "stop" in t:
                    sl_id = oo.get("id")
                    stop_px = (
                        oo.get("stopPrice") or info.get("stopPrice") or info.get("triggerPrice")
                    )
                    if stop_px is not None:
                        try:
                            recovered_sl = float(stop_px)
                        except (TypeError, ValueError):
                            pass
                elif "limit" in t and ro:
                    tp_id = oo.get("id")
                    if oo.get("price") is not None:
                        try:
                            recovered_tp = float(oo["price"])
                        except (TypeError, ValueError):
                            pass
            # Fallback: if open orders didn't reveal SL/TP (typical on
            # Binance testnet where STOP_MARKET lives in the algo queue
            # and is invisible via fetch_open_orders), approximate via
            # the configured SL_FRAC / TP_FRAC. This is the safest
            # downside: realised_pnl_R becomes meaningful (non-zero
            # risk denominator) even if the prices are nominal.
            from ictbot.settings import settings as _s

            if recovered_sl is None:
                recovered_sl = (
                    entry_price * (1 - _s.sl_frac)
                    if side == "BUY"
                    else entry_price * (1 + _s.sl_frac)
                )
            if recovered_tp is None:
                recovered_tp = (
                    entry_price * (1 + _s.tp_frac)
                    if side == "BUY"
                    else entry_price * (1 - _s.tp_frac)
                )
            stub = Order(
                pair=pair,
                side=side,
                entry=entry_price,
                sl=recovered_sl,
                tp=recovered_tp,
                qty=contracts,
                status="OPEN",
                is_reconciled=True,
            )
            stub.sl_order_id = sl_id
            stub.tp_order_id = tp_id
            self._orders[stub.id] = stub
