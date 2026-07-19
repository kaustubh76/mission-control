"""
DexScreener — FREE, keyless DEX-native market signals (the on-chain data replacement).

CMC's paid DEX/on-chain feeds (x402 `dex_search`, `cmc_onchain` pool quotes) are gone. DexScreener
serves the same class of DEX-native data — USD price, pooled liquidity, 24h DEX volume, price
change, and buy/sell tx counts — from a FREE public API with NO key and NO account
(`api.dexscreener.com`, ~60-300 req/min). We read BSC (PancakeSwap etc.) pairs for the contest
universe by contract address for precision, with symbol search as a fallback.

Best-effort + never-raise: any failure returns None/empty so callers degrade gracefully. Reads are
lightly TTL-cached in-process. This is the source for `regime_report`'s on-chain/DEX signals and the
richer (optional) vlayer DexScreener price proof. Candles + Fear&Greed live in `freefeeds.py`.
"""

from __future__ import annotations

import time as _time
from typing import Any

DEX_BASE = "https://api.dexscreener.com"
CHAIN = "bsc"

# Contest tokens → their canonical BSC (Binance-Peg / native-wrapped) ERC-20 contract addresses.
# Mirrors the CMC_IDS map pattern in cmc_agent_hub.py; a free swap keys on these instead of CMC ids.
BSC_TOKEN_ADDRS: dict[str, str] = {
    "BNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
    "ETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
    "LINK": "0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD",
    "UNI": "0xBf5140A22578168FD562DCcF235E5D43A02ce9B1",
    "AVAX": "0x1CE0c2827e2eF14D5C4f29a091d735A204794041",
    "DOT": "0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402",
    "DOGE": "0xbA2aE424d960c26247Dd6c32edC70B295c744C43",
}

_TTL_S = 60.0
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, produce):
    now = _time.time()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    val = produce()
    if val is not None:
        _cache[key] = (now, val)
    return val


def _get_json(path: str, timeout: float = 10.0):
    import requests

    try:
        r = requests.get(f"{DEX_BASE}{path}", headers={"Accept": "application/json"}, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _pairs_for_tokens(addrs: list[str]) -> list[dict] | None:
    """Raw DexScreener pairs for a list of BSC token addresses (batched, up to 30). None on failure.
    `GET /tokens/v1/{chain}/{addr1,addr2,...}` returns a flat list of pair objects."""
    if not addrs:
        return None
    joined = ",".join(addrs)

    def _fetch():
        body = _get_json(f"/tokens/v1/{CHAIN}/{joined}")
        # The endpoint may return a bare list or {"pairs": [...]}; normalize to a list.
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return body.get("pairs") or []
        return None

    return _cached(f"tok:{joined}", _TTL_S, _fetch)


def _best_pair(pairs: list[dict], addr: str) -> dict | None:
    """The deepest-liquidity BSC pair whose base token is `addr` (case-insensitive)."""
    addr_l = addr.lower()
    best, best_liq = None, -1.0
    for p in pairs or []:
        if str(p.get("chainId", "")).lower() != CHAIN:
            continue
        base = (p.get("baseToken") or {}).get("address", "")
        if str(base).lower() != addr_l:
            continue
        liq = float(((p.get("liquidity") or {}).get("usd")) or 0.0)
        if liq > best_liq:
            best, best_liq = p, liq
    return best


def _signal_from_pair(p: dict) -> dict[str, Any]:
    """Extract the public DEX signal fields from a DexScreener pair object."""
    liq = p.get("liquidity") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    txns = (p.get("txns") or {}).get("h24") or {}
    price = p.get("priceUsd")
    buys = int(txns.get("buys") or 0)
    sells = int(txns.get("sells") or 0)
    # marketCap when present, else FDV (DexScreener supplies one or the other per pair).
    mcap = p.get("marketCap") if p.get("marketCap") is not None else p.get("fdv")
    return {
        "price_usd": float(price) if price not in (None, "") else None,
        "liquidity_usd": float(liq.get("usd") or 0.0) or None,
        "volume_24h": float(vol.get("h24") or 0.0) or None,
        "price_change_h24": float(chg.get("h24")) if chg.get("h24") is not None else None,
        "txns_24h": (buys + sells) or None,
        "buys_24h": buys or None,
        "sells_24h": sells or None,
        "market_cap": float(mcap) if mcap not in (None, "") else None,
    }


def dex_signals(
    symbols: list[str] | tuple[str, ...] | None = None, *, with_windows: bool = False
) -> dict[str, dict[str, Any]]:
    """FREE DEX-native signals per contest symbol: {sym: {price_usd, liquidity_usd, volume_24h,
    price_change_h24, txns_24h, ...}}. Only known-address symbols are queried; unknown/failed symbols
    are omitted. Never raises — returns {} on total failure.

    `with_windows=True` enriches each token with `pct_7d`/`pct_30d` (free Binance daily klines) —
    DexScreener itself only exposes 24h. Off by default to keep the hot dashboard-poll path cheap."""
    syms = [s.upper() for s in (symbols or BSC_TOKEN_ADDRS.keys())]
    addrs = [BSC_TOKEN_ADDRS[s] for s in syms if s in BSC_TOKEN_ADDRS]
    pairs = _pairs_for_tokens(addrs)
    if not pairs:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for s in syms:
        addr = BSC_TOKEN_ADDRS.get(s)
        if not addr:
            continue
        bp = _best_pair(pairs, addr)
        if bp:
            rec = _signal_from_pair(bp)
            if with_windows:
                from ictbot.data import freefeeds

                win = freefeeds.window_returns(s)
                rec["pct_7d"] = win.get("pct_7d")
                rec["pct_30d"] = win.get("pct_30d")
            out[s] = rec
    return out


def token_price(symbol: str) -> float | None:
    """FREE USD price for `symbol` from its deepest BSC DEX pair — the DexScreener price fallback for
    `cmc_price` when the Binance ticker is unavailable. None on any miss."""
    su = symbol.upper()
    if su in ("USDT", "USD"):
        return 1.0
    sig = dex_signals([su]).get(su)
    return sig.get("price_usd") if sig else None


def dex_search(query: str) -> dict | None:
    """FREE DexScreener token/pair search — the replacement for `x402_cmc.dex_search` (which paid CMC
    per call). `GET /latest/dex/search?q=`. Returns the raw body ({schemaVersion, pairs:[...]}) or None."""
    if not query:
        return None
    import urllib.parse

    q = urllib.parse.quote(str(query))
    return _cached(f"search:{q}", _TTL_S, lambda: _get_json(f"/latest/dex/search?q={q}"))
