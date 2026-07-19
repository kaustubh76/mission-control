"""
Exchange protocol — the contract every venue adapter must satisfy.

Today: BinanceExchange + DeltaExchange + ReplayExchange (offline parquet
replay). Adding a new venue means implementing this Protocol.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class Exchange(Protocol):
    """A read-only OHLCV source. Phase 8 will add an OrderSink for writes."""

    name: str

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """Return OHLCV with columns time/open/high/low/close/volume.

        `time` must be a naive UTC datetime. Result is sorted oldest-first.
        Caller is responsible for pagination beyond what the venue supports
        directly; implementations are encouraged to paginate transparently.
        """
        ...

    def tick_size(self, symbol: str) -> float | None:
        """Return the price-tick precision, or None if the venue doesn't
        expose `precision.price` for this symbol. Used by `round_to_tick`
        to format SL/TP values correctly per market.
        """
        ...

    def contract_size(self, symbol: str) -> float:
        """Coins per contract. 1.0 = ccxt-unified symbols where qty is
        already in coin units (e.g. Binance USDT-M perps). On Delta
        perpetuals the contract is fractional (BTC = 0.001 BTC/contract,
        ETH = 0.01, etc.) — sizing must divide a coin-quantity by this
        number before placement. Defaults to 1.0 for venues without a
        contract concept.
        """
        ...

    def qty_step(self, symbol: str) -> float:
        """Minimum quantity increment. Delta = 1.0 (integer contracts);
        Binance BTC USDT-M = 0.001. Sizing must floor to this multiple
        before placement or the venue rejects the order.
        """
        ...

    def min_notional(self, symbol: str) -> float:
        """J2 (audit gap #10): minimum order value in quote currency.
        Orders sized below this are rejected by the exchange. Returns
        0.0 when the venue doesn't expose a minimum (caller treats as
        no constraint).
        """
        ...
