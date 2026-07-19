"""
Binance USDT-M Futures OHLCV adapter. Satisfies the venue-agnostic
`Exchange` protocol in `ictbot.data.exchange`, so the data factory can
hand it to any caller that already speaks pandas DataFrames.

Notes:
  - We use ccxt.binance() with `defaultType=future` so all read calls
    target fapi.binance.com (or testnet.binancefuture.com when testnet
    URLs are manually wired — handled by the LIVE broker, not here;
    OHLCV is public so it works on either host).
  - fetch_cvd is intentionally NOT implemented. `indicators/delta.py`
    checks `hasattr(exchange, "fetch_cvd")` and falls back to the
    candle-colour delta proxy if missing — that keeps signal generation
    working without paying the fetch_trades pagination cost on Binance.
"""

from __future__ import annotations

import logging
import time

import ccxt
import pandas as pd

PAGE_SIZE = 1000  # Binance Futures /fapi/v1/klines hard cap per call (silently caps higher values)
RETRY_COOLDOWN_SECONDS = 30

_log = logging.getLogger("ictbot.data.binance")

TF_MINUTES = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "1d": 1440,
}


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, ccxt.RateLimitExceeded):
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "-1003" in msg


class BinanceExchange:
    """Binance USDT-M Futures OHLCV reader."""

    name = "binance"

    def __init__(self, retry_cooldown: float = RETRY_COOLDOWN_SECONDS) -> None:
        self._client = ccxt.binance(
            {
                "enableRateLimit": True,
                "timeout": 8000,
                "options": {"defaultType": "future"},
            }
        )
        self._retry_cooldown = retry_cooldown
        self._markets_cache: dict | None = None

    # ---- market metadata --------------------------------------------------

    def _market_info(self, symbol: str) -> dict | None:
        if self._markets_cache is None:
            try:
                self._markets_cache = self._client.load_markets() or {}
            except Exception:
                self._markets_cache = {}
        return self._markets_cache.get(symbol)

    def tick_size(self, symbol: str) -> float | None:
        info = self._market_info(symbol) or {}
        prec = (info.get("precision") or {}).get("price")
        return float(prec) if prec is not None else None

    def contract_size(self, symbol: str) -> float:
        """Binance USDT-M perps: 1 contract = 1 base coin (e.g. 1 BTC)."""
        info = self._market_info(symbol) or {}
        cs = info.get("contractSize")
        return float(cs) if cs is not None else 1.0

    def qty_step(self, symbol: str) -> float:
        info = self._market_info(symbol) or {}
        step = (info.get("precision") or {}).get("amount")
        return float(step) if step is not None else 0.001

    def min_notional(self, symbol: str) -> float:
        info = self._market_info(symbol) or {}
        cost = (info.get("limits") or {}).get("cost") or {}
        mn = cost.get("min")
        return float(mn) if mn is not None else 0.0

    # ---- OHLCV ------------------------------------------------------------

    def _fetch_with_retry(self, *args, **kwargs) -> list:
        try:
            return self._client.fetch_ohlcv(*args, **kwargs)
        except Exception as exc:
            if not _is_rate_limit_error(exc):
                raise
            _log.warning(
                "Binance rate limit: %s — cooling %.0fs and retrying once",
                exc,
                self._retry_cooldown,
            )
            time.sleep(self._retry_cooldown)
            return self._client.fetch_ohlcv(*args, **kwargs)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        if limit <= PAGE_SIZE:
            return _to_df(self._fetch_with_retry(symbol, timeframe, limit=limit))

        mins = TF_MINUTES.get(timeframe)
        if mins is None:
            return _to_df(self._fetch_with_retry(symbol, timeframe, limit=PAGE_SIZE))

        pages: list[list] = []
        remaining = limit
        end_ts = self._client.milliseconds()
        # Safety cap: a healthy page is PAGE_SIZE bars, so the loop should
        # terminate in ceil(limit / PAGE_SIZE) iterations. Add a small buffer
        # for retries on partial pages, then hard-stop to prevent a runaway.
        max_iters = (limit // PAGE_SIZE) + 4
        for _ in range(max_iters):
            if remaining <= 0:
                break
            page_limit = min(PAGE_SIZE, remaining)
            since = end_ts - page_limit * mins * 60_000
            page = self._fetch_with_retry(symbol, timeframe, since=since, limit=page_limit)
            if not page:
                # Empty page = walked past historical horizon; nothing more to fetch.
                break
            pages.append(page)
            end_ts = page[0][0]
            remaining -= len(page)
            # A short page (len(page) < page_limit) used to terminate the loop,
            # but binance silently caps requests above 1000 — every page would
            # arrive "short" relative to e.g. an over-eager 1500 ask and the
            # loop would stop after one iteration. With PAGE_SIZE now matching
            # the actual API cap, healthy pages return exactly page_limit rows
            # and short pages legitimately signal end-of-history. We still let
            # the loop continue once on a short page to tolerate a brief gap,
            # since max_iters bounds total work.

        pages.reverse()
        all_rows = [row for p in pages for row in p]
        df = _to_df(all_rows)
        df = df.drop_duplicates(subset=["time"]).reset_index(drop=True)
        return df.tail(limit).reset_index(drop=True)


# ---- Module-level shim for legacy imports ----------------------------------

_default = BinanceExchange()
exchange = _default._client


def get_data(symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    return _default.fetch_ohlcv(symbol, timeframe, limit)


def _ensure_monotonic(df: pd.DataFrame) -> pd.DataFrame:
    if not df["time"].is_monotonic_increasing:
        _log.warning(
            "Binance OHLCV bars out of order — sorting (%d rows). "
            "Likely a pagination boundary; reordering is safe.",
            len(df),
        )
        df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    return df


def _to_df(ohlcv: list) -> pd.DataFrame:
    df = pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return _ensure_monotonic(df)
