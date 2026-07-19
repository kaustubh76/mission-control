"""
Delta Exchange adapter (https://www.delta.exchange).

Uses ccxt's `delta` client; satisfies the venue-agnostic `Exchange`
protocol in `ictbot.data.exchange`, so callers that already speak
pandas DataFrames stay untouched.

Venue-specific notes (captured here so the rest of the codebase doesn't
need to know):

  - Contract size is fractional on BTC/ETH (BTC = 0.001 BTC/contract,
    ETH = 0.01 ETH/contract); SOL/XRP/PAXG = 1 coin/contract. Callers
    that compute a coin-quantity must divide by `contract_size(symbol)`
    before placement.
  - Qty step = 1.0 for every Delta perpetual — orders must be integer
    contracts; the live broker floors to this multiple.
  - Tick sizes are small (XRP/SOL at 0.0001) so the auto-tick-size
    lookup is load-bearing here.
  - Rate limits trigger ccxt.RateLimitExceeded; single-retry with
    cooldown.
  - Pagination uses `since` ms windows; per-call cap is 1000 bars.
"""

from __future__ import annotations

import logging
import time

import ccxt
import pandas as pd

PAGE_SIZE = 1000

# Heuristic cooldown — Delta returns ccxt.RateLimitExceeded under
# sustained polling. Override via constructor if needed.
RETRY_COOLDOWN_SECONDS = 90


_log = logging.getLogger("ictbot.data.delta")

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
    """Detect ccxt rate-limit errors regardless of which subclass Delta raises."""
    if isinstance(exc, ccxt.RateLimitExceeded):
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "too many requests" in msg


def _is_transient_error(exc: Exception) -> bool:
    """Errors worth a quick second try before bubbling up as DATA INCOMPLETE.

    Delta returns intermittent NetworkError / RequestTimeout /
    ExchangeNotAvailable under load. The live observation that
    triggered this widening was a 4h candle fetch on BTC failing once
    mid-cycle and showing as 'fetch failed' in the TG card. Rate
    limit is handled separately (longer cooldown); these get a short
    sleep + retry.
    """
    if _is_rate_limit_error(exc):
        return False
    return isinstance(
        exc,
        (
            ccxt.NetworkError,
            ccxt.RequestTimeout,
            ccxt.ExchangeNotAvailable,
            ccxt.DDoSProtection,
        ),
    )


class DeltaExchange:
    """Delta perpetual OHLCV + market metadata implementing the Exchange protocol."""

    name = "delta"

    def __init__(
        self,
        retry_cooldown: float = RETRY_COOLDOWN_SECONDS,
        *,
        api_key: str = "",
        api_secret: str = "",
        client=None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            # ccxt's default request timeout (10 s on most versions) is the
            # total request budget, but on macOS the connect phase can sit
            # in SYN_SENT for tens of minutes when DNS hands back an IPv6
            # address the network can't reach. 8 s is plenty for OHLCV
            # against either Delta endpoint; a stuck SYN now fails fast
            # and the next pagination page is tried instead.
            opts: dict = {"enableRateLimit": True, "timeout": 8000}
            if api_key and api_secret:
                opts["apiKey"] = api_key
                opts["secret"] = api_secret
            self._client = ccxt.delta(opts)
        self._retry_cooldown = retry_cooldown
        self._tick_cache: dict[str, float | None] = {}
        self._contract_cache: dict[str, float] = {}
        self._step_cache: dict[str, float] = {}
        # Cache the markets dict itself — without this, every per-symbol
        # tick/contract/step lookup calls load_markets() again.
        self._markets_cache: dict | None = None

    # ---- CVD -----------------------------------------------------------------

    def fetch_cvd(self, symbol: str, since_ms: int, until_ms: int, page_size: int = 1000) -> float:
        """Sum aggressor-side delta across [since_ms, until_ms].

        Delta's fetch_trades returns the ccxt-unified
        {timestamp, side, amount} shape; pagination via `since` cursor.
        """
        if until_ms <= since_ms:
            return 0.0
        total = 0.0
        cursor = since_ms
        while cursor < until_ms:
            try:
                trades = self._client.fetch_trades(symbol, since=cursor, limit=page_size)
            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    raise
                time.sleep(self._retry_cooldown)
                trades = self._client.fetch_trades(symbol, since=cursor, limit=page_size)
            if not trades:
                break
            for t in trades:
                ts = int(t.get("timestamp") or 0)
                if ts > until_ms:
                    return round(total, 6)
                side = (t.get("side") or "").lower()
                amount = float(t.get("amount") or 0)
                if side == "buy":
                    total += amount
                elif side == "sell":
                    total -= amount
            new_cursor = int(trades[-1].get("timestamp") or cursor) + 1
            if new_cursor <= cursor:
                break
            cursor = new_cursor
        return round(total, 6)

    # ---- market metadata ----------------------------------------------------

    def _market(self, symbol: str) -> dict | None:
        """Lazy-load markets once (per-instance), return the entry for
        `symbol` or None. The markets dict is cached so subsequent
        symbol lookups don't refetch."""
        if self._markets_cache is None:
            try:
                self._markets_cache = self._client.load_markets() or {}
            except Exception:
                self._markets_cache = {}
        return self._markets_cache.get(symbol)

    def tick_size(self, symbol: str) -> float | None:
        """Price-tick precision from `precision.price`."""
        if symbol in self._tick_cache:
            return self._tick_cache[symbol]
        info = self._market(symbol) or {}
        prec = (info.get("precision") or {}).get("price")
        tick = float(prec) if prec is not None else None
        self._tick_cache[symbol] = tick
        return tick

    def contract_size(self, symbol: str) -> float:
        """Coins per contract. Defaults to 1.0 if the venue doesn't surface it."""
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]
        info = self._market(symbol) or {}
        cs = info.get("contractSize")
        size = float(cs) if cs is not None else 1.0
        self._contract_cache[symbol] = size
        return size

    def qty_step(self, symbol: str) -> float:
        """Quantity increment from `precision.amount`. Delta = 1.0."""
        if symbol in self._step_cache:
            return self._step_cache[symbol]
        info = self._market(symbol) or {}
        step = (info.get("precision") or {}).get("amount")
        val = float(step) if step is not None else 1.0
        self._step_cache[symbol] = val
        return val

    def min_notional(self, symbol: str) -> float:
        """J2 (audit gap #10): minimum order value in quote currency.

        Reads `limits.cost.min` (the ccxt-unified path for min-notional).
        Returns 0.0 when the venue doesn't expose it — caller treats 0.0
        as "no minimum" rather than as "reject everything".
        """
        info = self._market(symbol) or {}
        limits = info.get("limits") or {}
        cost = limits.get("cost") or {}
        min_cost = cost.get("min")
        return float(min_cost) if min_cost is not None else 0.0

    # ---- OHLCV --------------------------------------------------------------

    def _fetch_with_retry(self, *args, **kwargs) -> list:
        """Single-retry wrapper for fetch_ohlcv against Delta throttling +
        transient network/timeout errors. Non-transient errors (auth, bad
        symbol, etc.) bubble immediately."""
        try:
            return self._client.fetch_ohlcv(*args, **kwargs)
        except Exception as exc:
            if _is_rate_limit_error(exc):
                _log.warning(
                    "Delta rate limit hit: %s — cooling down %.0fs and retrying once",
                    exc,
                    self._retry_cooldown,
                )
                time.sleep(self._retry_cooldown)
                return self._client.fetch_ohlcv(*args, **kwargs)
            if _is_transient_error(exc):
                # Short backoff — these typically resolve in seconds.
                _log.warning(
                    "Delta transient error (%s): %s — retrying once after 2s",
                    type(exc).__name__,
                    exc,
                )
                time.sleep(2)
                return self._client.fetch_ohlcv(*args, **kwargs)
            raise

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        """Return OHLCV as a pandas DataFrame, oldest → newest.

        Delta-specific quirks this function papers over:
          1. The public history endpoint requires an explicit `since`; a
             bare `limit` call returns an empty page.
          2. Delta returns rows starting at `since` and walks forward.
             A naive `since = now - limit*tf` gives the OLDEST rows in
             that window, not the newest — fine on dense pairs, but on
             sparse perpetuals (PAXG, XRP off-hours) the result skews
             ancient because the window contains far more bars than
             `limit`.
          3. Sparse pairs only emit a candle when a trade prints, so a
             tight window can return zero rows.

        Strategy: always paginate. We walk windows of PAGE_SIZE bars
        backwards from `now` and stop once we have `limit` rows or the
        venue returns an empty page (no older history). The most-recent
        page lives at the tail, so `df.tail(limit)` after sort gives
        the freshest `limit` candles.
        """
        minutes_per_bar = TF_MINUTES.get(timeframe)
        if minutes_per_bar is None:
            ohlcv = self._fetch_with_retry(symbol, timeframe, limit=min(limit, PAGE_SIZE))
            return _to_df(ohlcv).tail(limit).reset_index(drop=True)

        ms_per_bar = minutes_per_bar * 60_000
        page_span_ms = PAGE_SIZE * ms_per_bar
        target_rows = max(limit, 1)
        end_ts = self._client.milliseconds()

        rows: list[list] = []
        seen: set[int] = set()
        empty_pages = 0
        # Hard cap so a chronically-sparse pair can't stall the scan loop
        # with an unbounded backward walk. limit=300 typically fits in 1–2
        # pages; 6 pages covers ~6000 bars of timeframe coverage which is
        # already 50× the strategy's MIN_BARS for the entry frame.
        MAX_PAGES = 6
        pages_fetched = 0
        while len(rows) < target_rows and pages_fetched < MAX_PAGES:
            pages_fetched += 1
            since = end_ts - page_span_ms
            page = self._fetch_with_retry(symbol, timeframe, since=since, limit=PAGE_SIZE)
            if not page:
                empty_pages += 1
                # Two empty pages in a row → venue has no older history
                # in this range; stop instead of looping forever on dead
                # symbols.
                if empty_pages >= 2:
                    break
                end_ts = since
                continue
            empty_pages = 0
            new_rows = [r for r in page if r and r[0] not in seen]
            for r in new_rows:
                seen.add(r[0])
            rows.extend(new_rows)
            oldest_in_page = page[0][0]
            # Step the window back; subtract one bar so consecutive
            # windows don't overlap on the boundary candle.
            next_end = oldest_in_page - ms_per_bar
            if next_end >= end_ts:
                break
            end_ts = next_end

        # Backward pagination accumulates rows newest-page → oldest-page,
        # which means the raw list is non-monotonic. Sort by epoch ms here
        # so `_to_df`'s monotonic check stays a real safety net (only
        # firing on genuine venue ordering bugs) rather than a noisy log
        # on every fetch.
        rows.sort(key=lambda r: r[0])
        df = _to_df(rows)
        df = df.drop_duplicates(subset=["time"]).reset_index(drop=True)
        return df.tail(limit).reset_index(drop=True)


def _ensure_monotonic(df: pd.DataFrame) -> pd.DataFrame:
    """J13 (audit gap #21): the backtest's np.searchsorted assumes ascending
    time. Sort defensively, with a warning if rows came back unsorted."""
    if not df["time"].is_monotonic_increasing:
        _log.warning(
            "OHLCV bars arrived out of order — sorting (%d rows). "
            "Pagination recovery from a rate-limit window likely cause.",
            len(df),
        )
        df = df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)
    return df


def _to_df(ohlcv: list) -> pd.DataFrame:
    df = pd.DataFrame(
        ohlcv,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return _ensure_monotonic(df)
