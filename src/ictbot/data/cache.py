"""
On-disk OHLCV cache (parquet).

Layout:  data/cache/{exchange}/{symbol-slug}/{timeframe}.parquet

`symbol-slug` replaces '/' and ':' with '_' so filesystem paths stay safe:
  BTC/USDT:USDT  →  BTC_USDT_USDT

The cache is append-only and idempotent on `time`. Re-fetching a window
that overlaps an existing cache merges rows by timestamp; the freshest
copy wins.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ictbot.settings import CACHE_DIR


def _slug(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_")


def cache_path(exchange: str, symbol: str, timeframe: str) -> Path:
    return CACHE_DIR / exchange / _slug(symbol) / f"{timeframe}.parquet"


def read(exchange: str, symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Return the cached DataFrame, or None if no cache exists yet."""
    p = cache_path(exchange, symbol, timeframe)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def write(exchange: str, symbol: str, timeframe: str, df: pd.DataFrame) -> Path:
    """Merge `df` into the cache, keeping the freshest rows on collision."""
    p = cache_path(exchange, symbol, timeframe)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = read(exchange, symbol, timeframe)
    if existing is None or existing.empty:
        out = df
    else:
        # Newer wins: append df last, then dedupe on time keeping last.
        out = (
            pd.concat([existing, df], ignore_index=True)
            .drop_duplicates(subset=["time"], keep="last")
            .sort_values("time")
            .reset_index(drop=True)
        )
    out.to_parquet(p, index=False)
    return p
