"""
FREE, keyless market-data feeds — the CoinMarketCap replacements.

CMC is no longer available, so the agent sources its live reads from free public APIs that need NO
API key and NO account:

  * Binance klines (`data-api.binance.vision`) — 4h + daily OHLCV candles. binance.vision is
    Binance's OWN public data host and, unlike `fapi.binance.com`, is NOT geo-blocked from
    US/cloud IPs (Render), so it works everywhere the agent runs.
  * Binance spot ticker — the latest execution price.
  * alternative.me Fear & Greed Index — the market-sentiment integer (0..100) that feeds the
    regime score, a drop-in for CMC's `/v3/fear-and-greed/latest`.

Everything here is REST-only, best-effort, and NEVER raises (any failure returns None/empty so the
caller degrades gracefully). Reads are lightly TTL-cached in-process so the 4s dashboard poll never
hammers a source. DEX-native signals (liquidity, on-chain volume) live in `dexscreener.py`.
"""

from __future__ import annotations

import time as _time

import pandas as pd

BINANCE_DATA_BASE = "https://data-api.binance.vision"
ALT_ME_FNG_URL = "https://api.alternative.me/fng/"

# Light in-process TTL cache (mirrors cmc_intel's _intel_net_cache pattern). Keyed by request.
_TTL_S = 60.0
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, produce):
    """Return a cached value if fresh, else produce+store it. `produce` may return None (not cached
    long — a transient miss should retry soon), or a value (cached for `ttl`)."""
    now = _time.time()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    val = produce()
    if val is not None:
        _cache[key] = (now, val)
    return val


def _pair(symbol: str) -> str:
    """Bare base symbol (e.g. 'BNB') → the Binance USDT spot pair ('BNBUSDT'). USDT/USD map to a
    dummy that callers short-circuit before reaching here."""
    return f"{symbol.upper()}USDT"


def binance_klines(symbol: str, interval: str = "4h", limit: int = 2500) -> pd.DataFrame | None:
    """FREE OHLCV candles for `symbol` from binance.vision (no key, geo-open). Returns the standard
    [time, open, high, low, close, volume] frame (floats, time-sorted) or None on any failure.

    `interval` is a Binance interval string ('4h', '1d', '1h', ...). `limit` is capped at Binance's
    1000/req; callers needing deeper history should page (the 4h/daily windows we use fit in 1000)."""
    import requests

    def _fetch():
        try:
            r = requests.get(
                f"{BINANCE_DATA_BASE}/api/v3/klines",
                params={"symbol": _pair(symbol), "interval": interval, "limit": min(int(limit), 1000)},
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
        return df[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)

    return _cached(f"kl:{symbol.upper()}:{interval}:{min(int(limit), 1000)}", _TTL_S, _fetch)


def binance_ticker_price(symbol: str) -> float | None:
    """FREE latest spot price (USD≈USDT) for `symbol` from binance.vision. None on any failure."""
    su = symbol.upper()
    if su in ("USDT", "USD"):
        return 1.0
    import requests

    def _fetch():
        try:
            r = requests.get(
                f"{BINANCE_DATA_BASE}/api/v3/ticker/price",
                params={"symbol": _pair(su)},
                timeout=8,
            )
            r.raise_for_status()
            p = float(r.json().get("price"))
            return p if p > 0 else None
        except Exception:
            return None

    return _cached(f"px:{su}", 15.0, _fetch)


def alt_me_fear_greed() -> int | None:
    """FREE crypto Fear & Greed Index (0..100) from alternative.me — the CMC F&G replacement.

    Response: {"data":[{"value":"63","value_classification":"Greed","timestamp":"...",...}]}.
    NOTE `value` is a STRING ("63"), so we parse it to int. None on any failure."""
    import requests

    def _fetch():
        try:
            r = requests.get(ALT_ME_FNG_URL, params={"limit": 1}, timeout=8)
            r.raise_for_status()
            v = int(str(r.json()["data"][0]["value"]).strip())
            return v if 0 <= v <= 100 else None
        except Exception:
            return None

    return _cached("fng", 300.0, _fetch)


def window_returns(symbol: str) -> dict:
    """FREE multi-window close-to-close returns for `symbol` from Binance DAILY klines — fills the
    `pct_7d`/`pct_30d` momentum fields DexScreener doesn't expose (it only has 24h). Returns
    `{"pct_7d": float|None, "pct_30d": float|None}` (%). Never raises; TTL-cached 300s (daily data)."""
    def _fetch():
        df = binance_klines(symbol, interval="1d", limit=35)
        if df is None or len(df) < 8:
            return {"pct_7d": None, "pct_30d": None}
        closes = df["close"].tolist()
        last = closes[-1]
        out: dict = {"pct_7d": None, "pct_30d": None}
        if last and len(closes) >= 8 and closes[-8]:
            out["pct_7d"] = round((last / closes[-8] - 1.0) * 100.0, 4)
        if last and len(closes) >= 31 and closes[-31]:
            out["pct_30d"] = round((last / closes[-31] - 1.0) * 100.0, 4)
        return out

    return _cached(f"win:{symbol.upper()}", 300.0, _fetch) or {"pct_7d": None, "pct_30d": None}


def coingecko_global() -> dict | None:
    """FREE, keyless global market metrics from CoinGecko — the macro read for market_overview.

    `GET api.coingecko.com/api/v3/global` =>
      {"data": {"market_cap_percentage": {"btc": 52.3, ...},
                "market_cap_change_percentage_24h_usd": -1.2, ...}}
    Returns {btc_dominance, mktcap_change_24h} (floats, %) or None on any failure."""
    import requests

    def _fetch():
        try:
            r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
            r.raise_for_status()
            d = (r.json() or {}).get("data") or {}
            btc = (d.get("market_cap_percentage") or {}).get("btc")
            chg = d.get("market_cap_change_percentage_24h_usd")
            if btc is None and chg is None:
                return None
            return {
                "btc_dominance": round(float(btc), 2) if btc is not None else None,
                "mktcap_change_24h": round(float(chg), 2) if chg is not None else None,
            }
        except Exception:
            return None

    return _cached("cg_global", 300.0, _fetch)


def alt_me_fng_classification() -> str | None:
    """The textual F&G classification (e.g. 'Greed') from alternative.me. Best-effort; None on miss."""
    import requests

    def _fetch():
        try:
            r = requests.get(ALT_ME_FNG_URL, params={"limit": 1}, timeout=8)
            r.raise_for_status()
            return str(r.json()["data"][0]["value_classification"]) or None
        except Exception:
            return None

    return _cached("fng_cls", 300.0, _fetch)
