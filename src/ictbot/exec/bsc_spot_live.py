"""
TwakSpotBroker — a long-only spot PORTFOLIO REBALANCER for BSC, signed by TWAK.

The momentum allocator emits target weights over {tokens} (rest = USDT). This
broker turns a target-weight vector into the minimal set of spot swaps that move
the live book toward it, executed through a TwakClient (sim or live). There are no
SL/TP bracket orders — an AMM swap has no native stop, and the allocator's risk
control is the deployment cap + cash filter + the runtime's drawdown halt, not
per-trade stops. So this is deliberately a rebalancer, not a bracket trader.

Order of operations each rebalance: compute NAV and current weights, SELL the
overweight legs to USDT first (freeing quote), then BUY the underweight legs from
USDT. Sub-threshold deltas are skipped to avoid dust churn + needless friction.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ictbot.exec.twak_client import QUOTE, SwapResult

log = logging.getLogger(__name__)

__all__ = ["TwakSpotBroker", "RebalanceReport", "LiveTradingDisabled"]


class LiveTradingDisabled(RuntimeError):
    """Raised when a LIVE swap is attempted without ENABLE_LIVE_TRADING=true."""


@dataclass
class RebalanceReport:
    nav_before: float
    nav_after: float
    target: dict[str, float]
    weights_before: dict[str, float]
    weights_after: dict[str, float]
    swaps: list[SwapResult] = field(default_factory=list)

    @property
    def fees_usd(self) -> float:
        return sum(s.fee_paid for s in self.swaps if s.ok)

    @property
    def n_swaps(self) -> int:
        return sum(1 for s in self.swaps if s.ok)

    @property
    def n_failed(self) -> int:
        return sum(1 for s in self.swaps if not s.ok)

    @property
    def failed_swaps(self) -> list[SwapResult]:
        return [s for s in self.swaps if not s.ok]


class TwakSpotBroker:
    name = "bsc_spot"

    def __init__(
        self,
        client,
        *,
        tokens,
        quote: str = QUOTE,
        min_rebal_frac: float = 0.02,  # skip moves smaller than 2% of NAV
        min_swap_usd: float = 1.0,  # absolute floor: skip dust swaps < $1 notional
        live: bool = False,
        live_enabled: bool = False,
        dry_run: bool = False,
        settle_seconds: float = 0.0,  # LIVE settlement slack before a balance-dependent step (see _settle)
    ) -> None:
        self.client = client
        self.tokens = list(tokens)
        self.quote = quote
        self.min_rebal_frac = min_rebal_frac
        self.min_swap_usd = min_swap_usd
        # On-chain (real or quote-only) flag — gates the LIVE-only settlement slack in _settle.
        self.live = bool(live)
        self.settle_seconds = settle_seconds
        # Quote-only mode: every swap is sent with execute=False, so the broker runs the
        # FULL loop against the real CLI (real balances + router quotes) but NEVER signs or
        # spends. It needs the live client, so it is exempt from the ENABLE_LIVE_TRADING
        # gate below (nothing can execute).
        self.dry_run = dry_run
        # Live execution gate (mirrors the delta/binance brokers' LiveTradingDisabled).
        if live and not live_enabled and not dry_run:
            raise LiveTradingDisabled(
                "Refusing to construct a LIVE TwakSpotBroker without "
                "ENABLE_LIVE_TRADING=true. Use sim mode for dry-runs."
            )

    # --- read-side --------------------------------------------------------- #
    def prices(self) -> dict[str, float]:
        return {t: self.client.price(t) for t in self.tokens}

    def holdings_usd(self, prices: dict[str, float]) -> dict[str, float]:
        return {t: self.client.balance(t) * prices[t] for t in self.tokens}

    def nav(self, prices: dict[str, float]) -> float:
        return self.client.balance(self.quote) + sum(self.holdings_usd(prices).values())

    def current_weights(self, prices: dict[str, float]) -> dict[str, float]:
        nav = self.nav(prices)
        if nav <= 0:
            return {t: 0.0 for t in self.tokens}
        h = self.holdings_usd(prices)
        return {t: h[t] / nav for t in self.tokens}

    def positions(self) -> dict[str, float]:
        """Open token balances (qty), excluding USDT/dust — protocol-ish accessor."""
        return {t: self.client.balance(t) for t in self.tokens if self.client.balance(t) > 0}

    # --- write-side -------------------------------------------------------- #
    def _settle(self) -> None:
        """LIVE-only settlement slack: optionally pause so a just-SUBMITTED swap MINES before the next
        balance-dependent step — the BUY loop reading USDT (else it underfunds on a not-yet-credited
        SELL), or an emergency_flatten retry re-reading the residual (else it could re-sell a pending
        leg). DEFAULT 0 (off): `twak swap` returns the mined output amount + txHash under a 180s _run
        timeout, i.e. it BLOCKS until mined, so balance() already reflects the swap and no slack is
        needed. An operator sets settle_seconds > 0 ONLY if a real swap proves twak returns pre-mine
        (verify at go-live via scripts/live_swap_smoke.py: does balance() change immediately?). No-op in
        sim/quote-only (no real on-chain tx)."""
        if not (self.live and not self.dry_run) or self.settle_seconds <= 0:
            return
        time.sleep(self.settle_seconds)

    def rebalance(
        self, target: dict[str, float], prices: dict[str, float] | None = None
    ) -> RebalanceReport:
        """Move the book toward `target` (weights over tokens; rest USDT)."""
        prices = prices or self.prices()
        nav0 = self.nav(prices)
        w_before = self.current_weights(prices)
        h_usd = self.holdings_usd(prices)
        thresh = self.min_rebal_frac * nav0 if nav0 > 0 else 0.0

        deltas = {t: target.get(t, 0.0) * nav0 - h_usd[t] for t in self.tokens}
        swaps: list[SwapResult] = []

        # SELL overweight first (token -> USDT), so quote is available for buys.
        # A failed swap now returns ok=False (it does NOT raise), so the loop always
        # completes and every attempt — including failures — is captured in `swaps`.
        for t in self.tokens:
            d = deltas[t]
            if d < -thresh:
                qty = min(self.client.balance(t), (-d) / prices[t])
                notional = qty * prices[t]
                if qty > 0 and notional >= self.min_swap_usd:
                    swaps.append(self.client.swap(t, self.quote, qty, execute=not self.dry_run))
        # LIVE: optional settlement slack so SELL proceeds credit before the BUY loop reads USDT
        # (else a not-yet-mined SELL underfunds the BUY). No-op by default / in sim — see _settle.
        if any(s.ok for s in swaps):
            self._settle()
        # BUY underweight (USDT -> token), capped by available USDT. If a sell above
        # failed, the freed quote is simply smaller, so buys shrink — never overspend.
        for t in self.tokens:
            d = deltas[t]
            if d > thresh:
                spend = min(d, self.client.balance(self.quote))
                if spend >= self.min_swap_usd:
                    swaps.append(self.client.swap(self.quote, t, spend, execute=not self.dry_run))

        prices_after = self.prices()
        return RebalanceReport(
            nav_before=nav0,
            nav_after=self.nav(prices_after),
            target=dict(target),
            weights_before=w_before,
            weights_after=self.current_weights(prices_after),
            swaps=swaps,
        )

    def emergency_flatten(
        self, prices: dict[str, float] | None = None, *, retries: int = 3, backoff: float = 1.0
    ) -> list[SwapResult]:
        """Sell every token back to USDT (drawdown-halt / shutdown safety).

        Each leg is best-effort and strictly ONE-DIRECTIONAL (token -> USDT only —
        it never opens or flips a position). A failed leg is RETRIED up to `retries`
        times with exponential backoff, re-reading the live balance each attempt (a
        leg may have partially filled), before the final CRITICAL log. One leg
        failing does not abort the others, and the method NEVER raises. The residual
        exposure is logged + returned so an operator can finish a stubborn leg.
        """
        prices = prices or self.prices()
        out: list[SwapResult] = []
        for t in self.tokens:
            qty = self.client.balance(t)
            if qty <= 0:
                continue
            res = self.client.swap(t, self.quote, qty, execute=not self.dry_run)
            attempt = 0
            while not res.ok and attempt < retries:
                time.sleep(backoff * (2**attempt))
                # LIVE: optional slack so a pending/partial sell mines before re-reading, so the retry
                # sells only the true residual and never double-sells a landed tx. No-op by default/sim.
                self._settle()
                qty = self.client.balance(t)  # re-read: the leg may have partially filled
                if qty <= 0:  # balance drained -> nothing left to sell
                    break
                res = self.client.swap(t, self.quote, qty, execute=not self.dry_run)
                attempt += 1
            out.append(res)
        failed = [s for s in out if not s.ok]
        if failed:
            log.critical(
                "PARTIAL emergency flatten after retries: %d/%d sell(s) failed; residual exposure=%s",
                len(failed),
                len(out),
                self.positions(),
            )
        return out
