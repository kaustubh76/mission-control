"""
Exchange factory — single entry point for analyzer / backtest / scanner.

`settings.exchange` picks the venue at import time. Two venues today:
  - "delta"   → ictbot.data.delta.DeltaExchange (mainnet target)
  - "binance" → ictbot.data.binance.BinanceExchange (testnet, default
                for the ongoing testing window)

The factory exposes `get_default_exchange()` and a `get_data(symbol, tf,
limit)` shim so callers can just `from ictbot.data.factory import
get_data` and get the configured venue for free.

The default-exchange instance is constructed lazily so importing this
module doesn't open a network connection during test collection.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from ictbot.settings import settings


class _ExchangeLike(Protocol):
    name: str

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame: ...
    def tick_size(self, symbol: str) -> float | None: ...
    def contract_size(self, symbol: str) -> float: ...
    def qty_step(self, symbol: str) -> float: ...
    def min_notional(self, symbol: str) -> float: ...


_default: _ExchangeLike | None = None


def _build_default() -> _ExchangeLike:
    """Construct the configured venue's adapter. Imports are deferred so
    one-venue test environments don't pay the other venue's import cost."""
    name = settings.exchange.lower()
    if name == "delta":
        from ictbot.data.delta import DeltaExchange

        return DeltaExchange(
            api_key=settings.delta_api_key,
            api_secret=settings.delta_api_secret,
        )
    if name == "binance":
        from ictbot.data.binance import BinanceExchange

        return BinanceExchange()
    raise ValueError(f"Unknown EXCHANGE={settings.exchange!r} — expected 'delta' or 'binance'")


def get_default_exchange() -> _ExchangeLike:
    """Return the lazy singleton for the configured venue."""
    global _default
    if _default is None:
        _default = _build_default()
    return _default


def set_default_exchange(exchange: _ExchangeLike) -> None:
    """Override the singleton (testing hook). Pass a fresh
    DeltaExchange / BinanceExchange (or any object satisfying the
    protocol).
    """
    global _default
    _default = exchange


def reset_default_exchange() -> None:
    """Forget the cached singleton so the next `get_default_exchange()`
    call rebuilds from current settings. Used by tests that mutate
    settings.exchange via monkeypatch.
    """
    global _default
    _default = None


def get_data(symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    """Shim that delegates to whichever exchange is configured."""
    return get_default_exchange().fetch_ohlcv(symbol, timeframe, limit)
