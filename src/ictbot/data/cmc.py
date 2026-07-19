"""
CoinMarketCap Agent-Hub data feed (+ Binance public fallback).

This module is the keyed CMC **Pro API** path: the live PRICE quote and the FEAR &
GREED regime read when a CMC_API_KEY is configured. The CMC **AI Agent Hub x402**
paid-data path (on-chain USDC micropayments, no key) lives in `x402_cmc.py` and falls
back to these functions; the allocator prefers x402 when `X402_ENABLED` is set. The actual 4h OHLCV *candles* the momentum allocator
ranks on come from Binance's public endpoint (no key, deep history) — CMC's
intraday OHLCV is gated to higher tiers, so Binance is the pragmatic, proven
candle source and the universal fallback. Either way the frame shape is identical
to the rest of the codebase: columns [time, open, high, low, close, volume].

Nothing here signs or trades — data only. Execution is TWAK's job.
"""

from __future__ import annotations

import json
import os
import time as _time

import pandas as pd

from ictbot.data import cache
from ictbot.data.cmc_client import CMC
from ictbot.settings import DATA_DIR, settings

CMC_BASE = "https://pro-api.coinmarketcap.com"
MIN_BARS = 250

# CMC-native 4h candles accumulated by scripts/cmc_stream.py (the WebSocket feed).
_CMC_4H_BAR_SECONDS = 4 * 3600
_CMC_4H_PARTIAL = DATA_DIR / "cache" / "cmc_4h_partial.json"


def _resolve_key(api_key: str | None) -> str:
    """Resolve the CMC key: explicit arg -> os.environ -> settings.cmc_api_key.

    pydantic-settings loads `.env` into `settings`, NOT os.environ, so a bare
    `cmc_price('BNB')` would otherwise miss a key that lives only in .env."""
    if api_key:
        return api_key
    env = os.environ.get("CMC_API_KEY", "")
    if env:
        return env
    try:
        from ictbot.settings import settings

        return settings.cmc_api_key or ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# CMC Pro API (keyed) — price + fear/greed. All HTTP routes through the hardened
# CmcClient (CMC): rate-limit + credit budget + retry + TTL cache. The public
# signatures below are UNCHANGED so every existing caller keeps working.
# --------------------------------------------------------------------------- #
def _cmc_get(path: str, params: dict, api_key: str, timeout: float = 15.0) -> dict:
    """Deprecated shim — now routes through CMC.get. Raises on failure to preserve the
    old 'raises on error' contract for any external caller; internal callers below use
    CMC.get directly (which degrades to None)."""
    body = CMC.get(path, params, data_class="generic", api_key=api_key)
    if body is None:
        raise RuntimeError(f"CMC request failed: {path}")
    return body


def cmc_price(symbol: str, api_key: str | None = None) -> float | None:
    """Latest USD price. None if unavailable. (Name kept for caller compatibility.)

    FREE_DATA (default): the free Binance spot ticker, then a DexScreener DEX-price fallback — CMC
    quotes are gone. Falls back to the CMC quotes path only if both miss (legacy, needs a key)."""
    if settings.free_data and not settings.cmc_only:
        from ictbot.data import dexscreener, freefeeds

        p = freefeeds.binance_ticker_price(symbol)
        if p is not None and p > 0:
            return p
        p = dexscreener.token_price(symbol)
        if p is not None and p > 0:
            return p
    body = CMC.get(
        "/v2/cryptocurrency/quotes/latest",
        {"symbol": symbol},
        data_class="quotes",
        est_credits=1,
        api_key=api_key,
    )
    if not body:
        return None
    try:
        return float(body["data"][symbol][0]["quote"]["USD"]["price"])
    except (KeyError, ValueError, TypeError, IndexError):
        return None


def fear_greed(api_key: str | None = None) -> int | None:
    """Latest Fear & Greed index (0-100). None if unavailable.

    FREE_DATA (default): read the free, keyless alternative.me index — CMC's F&G endpoint is gone.
    Falls back to the CMC path only if alternative.me misses AND a key is configured (legacy)."""
    if settings.free_data and not settings.cmc_only:
        from ictbot.data.freefeeds import alt_me_fear_greed

        v = alt_me_fear_greed()
        if v is not None:
            return v
    body = CMC.get("/v3/fear-and-greed/latest", {}, data_class="fear_greed", api_key=api_key)
    if not body:
        return None
    try:
        return int(body["data"]["value"])
    except (KeyError, ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Candle source: Binance public 4h (cache-aware) — the universal fallback
# --------------------------------------------------------------------------- #
def _ccxt_live_4h(exchange: str, ccxt_sym: str, limit: int) -> pd.DataFrame | None:
    """Live 4h OHLCV from a geo-open exchange via ccxt (e.g. 'bybit'). The cloud-host fallback for
    when Binance USDT-M futures (fapi.binance.com) is 451 geo-blocked — Bybit serves public klines
    from US/cloud IPs that Binance rejects. Standard [time,o,h,l,c,v] frame or None."""
    try:
        import ccxt

        ex = getattr(ccxt, exchange)({"enableRateLimit": True, "timeout": 8000})
        rows = ex.fetch_ohlcv(ccxt_sym, "4h", limit=min(limit, 1000))
    except Exception:
        return None
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ms", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ms"], unit="ms")
    return df[["time", "open", "high", "low", "close", "volume"]]


def _binance_vision_4h(symbol: str, limit: int) -> pd.DataFrame | None:
    """Live 4h spot klines from data-api.binance.vision — Binance's OWN public data host, which is
    NOT geo-blocked like fapi.binance.com. Last-resort backstop for cloud hosts (Render) when even
    Bybit is blocked. Same Binance data; standard [time,o,h,l,c,v] frame or None."""
    import requests

    try:
        r = requests.get(
            "https://data-api.binance.vision/api/v3/klines",
            params={"symbol": f"{symbol}USDT", "interval": "4h", "limit": min(limit, 1000)},
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return None
    if not rows:
        return None
    cols = ["ms", "open", "high", "low", "close", "volume", "ct", "qv", "n", "tb", "tq", "ig"]
    df = pd.DataFrame(rows, columns=cols)
    df["time"] = pd.to_datetime(df["ms"], unit="ms")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df[["time", "open", "high", "low", "close", "volume"]]


def fetch_4h(symbol: str, limit: int = 2500) -> pd.DataFrame | None:
    """4h OHLCV for `symbol` (bare base, e.g. 'BNB'): live Binance futures -> binance cache ->
    geo-open live fallbacks (Bybit, then Binance's public data mirror) -> bybit replay cache.
    Returns the standard [time,o,h,l,c,v] frame or None.

    The geo-open fallbacks exist because Binance USDT-M futures (fapi.binance.com) returns HTTP 451
    from US/cloud datacenters (e.g. Render), where the on-disk cache is also empty on an ephemeral
    FS. CMC has no intraday OHLCV on our tier, so a non-blocked exchange feed is the only way those
    hosts get 4h candles. The fallbacks only run AFTER the canonical Binance-futures path fails, so
    a developer/CI run (Binance reachable) is unaffected — `validate_allocator` stays bit-for-bit.

    ZERO-CEX firewall: when CMC_ONLY is set (the CMC-native contest config) this function is the
    single chokepoint every exchange path funnels through — the allocator's binance_4h branch,
    price()'s last-close fallback, and the validation scripts. We RAISE here rather than silently
    serve exchange data, so any accidental CEX reach on the contest config fails loud, not quiet."""
    if settings.cmc_only:
        raise RuntimeError(
            "CMC_ONLY is set but a CEX candle path (cmc.fetch_4h -> Binance/Bybit) was reached. "
            "This is the zero-CEX firewall: the contest arm (momentum_cmc) must source candles "
            "from cmc_4h_close_matrix and price from cmc_price / fetch_cmc_4h only. A reach here "
            "means a non-CMC arm or a stale code path ran under the CMC contest config."
        )
    ccxt_sym = f"{symbol}/USDT:USDT"
    try:
        from ictbot.data.binance import BinanceExchange

        df = BinanceExchange().fetch_ohlcv(ccxt_sym, "4h", limit)
        if df is not None and len(df) >= MIN_BARS:
            cache.write("binance", ccxt_sym, "4h", df)
            return df.tail(limit).reset_index(drop=True)
    except Exception:
        pass
    try:
        df = cache.read("binance", ccxt_sym, "4h")
        if df is not None and len(df) >= MIN_BARS:
            return df.tail(limit).reset_index(drop=True)
    except Exception:
        pass
    # Geo-open live fallbacks (cloud hosts where fapi is 451 + no cache): Bybit first (non-Binance),
    # then Binance's own public data mirror. Deliberately NOT cached under the "binance" key — the
    # canonical cache stays pure Binance-futures data, so a dev/CI run can never read fallback data
    # (validate_allocator bit-for-bit). A daily tick re-fetching 8 tokens is cheap.
    for _src in (
        lambda: _ccxt_live_4h("bybit", ccxt_sym, limit),
        lambda: _binance_vision_4h(symbol, limit),
    ):
        try:
            df = _src()
            if df is not None and len(df) >= MIN_BARS:
                return df.tail(limit).reset_index(drop=True)
        except Exception:
            pass
    try:
        from ictbot.data.replay import ReplayExchange

        df = ReplayExchange(exchange="bybit").fetch_ohlcv(ccxt_sym, "4h", limit)
        if df is not None and len(df) >= MIN_BARS:
            return df
    except Exception:
        pass
    return None


def fetch_daily_cmc(symbol: str, days: int = 730) -> pd.DataFrame | None:
    """CMC-native DAILY OHLCV (commercial-licensed, up to 24 months) as the standard
    [time,o,h,l,c,v] frame, cached under exchange 'cmc'. A long-window source for macro
    regime + a CMC-native alternative to the Binance candles. Returns None when intel is
    disabled / unavailable, falling back to any prior cached CMC daily."""
    from ictbot.data.cmc_intel import daily_ohlcv

    df = daily_ohlcv(symbol, days=days)
    if df is not None and len(df):
        cache.write("cmc", f"{symbol}/USD", "1d", df)
        return df.reset_index(drop=True)
    try:
        cached = cache.read("cmc", f"{symbol}/USD", "1d")
        if cached is not None and len(cached):
            return cached.reset_index(drop=True)
    except Exception:
        pass
    return None


def daily_close_matrix(tokens=None, days: int = 730):
    """Aligned CMC **daily** close matrix (the CMC-native candle feed that drives the
    `momentum_cmc` strategy's selection): reuses `fetch_daily_cmc` (24-month, commercial-licensed,
    GEO-OPEN — works from cloud hosts where Binance 4h is 451-blocked) + the shared
    `align_close_matrix`. Returns a DataFrame indexed by time with symbol columns in CONTEST_TOKENS
    order (empty frame if CMC daily is unavailable for the universe). Requires CMC_INTEL_ENABLED.
    Lazy imports avoid a data<->strategy/engine import cycle."""
    from ictbot.engine.portfolio_replay import align_close_matrix
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    toks = tuple(tokens) if tokens is not None else CONTEST_TOKENS
    frames = {t: fetch_daily_cmc(t, days=days) for t in toks}
    return align_close_matrix(frames, toks)


def fetch_cmc_4h(symbol: str, limit: int = 2500):
    """CMC-native 4h OHLCV for `symbol` — candles ACCUMULATED from CMC's live WebSocket feed by
    scripts/cmc_stream.py (cache exchange='cmc', symbol='<sym>/USDT', tf='4h'), plus the in-progress
    (partial) bar so a live tick sees the freshest CMC price. Standard [time,o,h,l,c,v] frame or None."""
    df = cache.read("cmc", f"{symbol}/USDT", "4h")
    try:
        b = json.loads(_CMC_4H_PARTIAL.read_text(encoding="utf-8")).get(symbol)
    except Exception:
        b = None
    if b:
        row = pd.DataFrame(
            [
                {
                    "time": pd.Timestamp(int(b["start"]), unit="s"),
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "volume": float(b.get("vol") or 0.0),
                }
            ]
        )
        df = row if df is None else pd.concat([df, row], ignore_index=True)
    # FREE_DATA: the CMC WebSocket stream is gone, so the 'cmc' 4h cache is empty. Source real 4h
    # candles from the free Binance path instead (fetch_4h — already Binance-backed, geo-open). Gated
    # `not cmc_only` so the CMC_ONLY firewall (which makes fetch_4h RAISE) is never tripped here.
    if (df is None or len(df) < MIN_BARS) and settings.free_data and not settings.cmc_only:
        binance = fetch_4h(symbol, limit=limit)
        if binance is not None and len(binance):
            return binance.tail(limit).reset_index(drop=True)
    if df is None or not len(df):
        return None
    return (
        df.drop_duplicates(subset=["time"], keep="last")
        .sort_values("time")
        .reset_index(drop=True)
        .tail(limit)
        .reset_index(drop=True)
    )


def seed_cmc_4h_from_daily(tokens=None, days: int = 730) -> int:
    """Cold-start: backfill the cmc 4h cache from CMC DAILY closes forward-filled onto the PAST 4h grid,
    so momentum_cmc has lookback history before the live stream accumulates. 100% CMC-sourced; momentum
    (close-to-close) stays accurate. `cache.write` dedups on time keeping the FRESHEST, so real streamed
    bars overwrite the seed. Only seeds COMPLETED past bars. Returns the count of tokens seeded."""
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    toks = tuple(tokens) if tokens is not None else CONTEST_TOKENS
    now_s = int(_time.time())
    seeded = 0
    for sym in toks:
        d = fetch_daily_cmc(sym, days=days)
        if d is None or not len(d):
            continue
        rows = []
        for _, r in d.iterrows():
            day0 = int(pd.Timestamp(r["time"]).timestamp())
            c = float(r["close"])
            for k in range(6):  # six 4h slots per UTC day
                t = day0 + k * _CMC_4H_BAR_SECONDS
                if t + _CMC_4H_BAR_SECONDS > now_s:
                    continue  # don't seed the current/future bar — the stream builds it live
                rows.append(
                    {
                        "time": pd.Timestamp(t, unit="s"),
                        "open": c,
                        "high": c,
                        "low": c,
                        "close": c,
                        "volume": 0.0,
                    }
                )
        if rows:
            cache.write("cmc", f"{sym}/USDT", "4h", pd.DataFrame(rows))
            seeded += 1
    return seeded


def cmc_4h_close_matrix(tokens=None, *, seed: bool = True):
    """Aligned CMC **4h** close matrix (the CMC-native feed for momentum_cmc): the stream-accumulated
    cache + partial bar. If the store is thin (cold start), backfill ONCE from CMC daily forward-filled
    to the 4h grid. Returns a DataFrame indexed by time with symbol columns in CONTEST_TOKENS order."""
    from ictbot.engine.portfolio_replay import align_close_matrix
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    toks = tuple(tokens) if tokens is not None else CONTEST_TOKENS
    frames = {t: fetch_cmc_4h(t) for t in toks}
    have = max((len(f) for f in frames.values() if f is not None), default=0)
    if seed and have < MIN_BARS:
        seed_cmc_4h_from_daily(toks)
        frames = {t: fetch_cmc_4h(t) for t in toks}
    return align_close_matrix(frames, toks)


def price(symbol: str, api_key: str | None = None) -> float:
    """Live price for execution sizing: CMC quote if keyed, else last 4h close.

    Raises if neither source yields a price (the caller must not trade blind).

    Under CMC_ONLY the last-close fallback is the CMC 4h stream cache (fetch_cmc_4h),
    NOT fetch_4h (which would raise on the CEX firewall) — so a transient CMC-quote
    miss degrades to the freshest CMC stream close instead of skipping the whole tick
    (which would starve the >=7-trade/week floor). 100% CMC either way."""
    if symbol.upper() in ("USDT", "USD"):
        return 1.0
    p = cmc_price(symbol, api_key)
    if p is not None and p > 0:
        return p
    if settings.cmc_only:
        # CMC-native fallback: last close from the CMC 4h stream cache (no CEX).
        df = fetch_cmc_4h(symbol, limit=MIN_BARS)
        if df is not None and len(df):
            return float(df["close"].iloc[-1])
        raise RuntimeError(
            f"no CMC price available for {symbol} (cmc_price quote + cmc_4h stream both empty)"
        )
    df = fetch_4h(symbol, limit=MIN_BARS)
    if df is not None and len(df):
        return float(df["close"].iloc[-1])
    raise RuntimeError(f"no price available for {symbol} (CMC + Binance both failed)")


def price_fn(api_key: str | None = None):
    """Return a `price(token)->float` callable for the TWAK client / broker."""
    return lambda token: price(token, api_key)
