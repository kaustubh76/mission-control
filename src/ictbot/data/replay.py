"""
ReplayExchange — reads OHLCV from the on-disk parquet cache only. No
network. Use it to backtest deterministically against a snapshot of
history without re-hitting the exchange.

Usage:
  >>> from ictbot.data.replay import ReplayExchange
  >>> ex = ReplayExchange(exchange="binance")
  >>> df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=5000)

Populate the cache via `data.cache.write(...)` or by running
`CachedExchange.fetch_ohlcv(...)` against a live BinanceExchange first.
"""

from __future__ import annotations

import pandas as pd

from ictbot.data import cache


class ReplayMiss(Exception):
    """Raised when the cache has no data for the requested (symbol, tf)."""


class ReplayExchange:
    """Exchange protocol impl that only reads from the parquet cache."""

    def __init__(self, exchange: str = "binance") -> None:
        self.name = f"replay:{exchange}"
        self._exchange = exchange

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        df = cache.read(self._exchange, symbol, timeframe)
        if df is None or df.empty:
            raise ReplayMiss(
                f"no cached data for {symbol} {timeframe} (populate via ictbot.data.cache.write)"
            )
        return df.tail(limit).reset_index(drop=True)


class CachedExchange:
    """Wraps any Exchange — on every fetch, writes through to the cache.

    Reads always go to the upstream venue (so we don't get stale data).
    Use ReplayExchange to read cache-only.
    """

    def __init__(self, upstream, exchange_name: str | None = None) -> None:
        self._upstream = upstream
        self.name = exchange_name or getattr(upstream, "name", "unknown")

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        df = self._upstream.fetch_ohlcv(symbol, timeframe, limit)
        cache.write(self.name, symbol, timeframe, df)
        return df
