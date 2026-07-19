"""
CMC market-intelligence fetchers (Phase 2) — the "super powers" the Startup tier unlocks.

Every function here:
  - routes through the hardened `CMC` client (rate-limit + credit budget + retry + cache),
  - is GATED by `settings.cmc_intel_enabled` (default OFF → the agent is unchanged),
  - consults the Phase-0 capability map (`data/journal/cmc_capability.json`) and
    short-circuits any endpoint marked unavailable, so a tier-gated endpoint never
    burns credits looping on a 403,
  - returns None / [] / {} on any failure (never raises).

Two consumers:
  - `build_regime_intel()` → the inputs the enhanced regime model (Phase 3) folds in.
  - `market_intel_snapshot()` → everything the dashboard Market-Intelligence panel shows.
Neither drives a trade unless the A/B flags (`cmc_regime_enhanced`, `alloc_universe_tilt`)
are explicitly enabled.
"""

from __future__ import annotations

import json
import math
import time

import pandas as pd

from ictbot.data.cmc_client import CMC
from ictbot.settings import JOURNAL_DIR, settings

_CAP_PATH = JOURNAL_DIR / "cmc_capability.json"
_cap_cache: dict = {"ts": 0.0, "map": None}

# Whether the most recent token_changes() served the universe tilt from the local CMC-WS quote
# snapshot ("cmc_ws", 0 credits) or the REST quotes/latest fallback ("rest"). Read by
# run_allocator for the tick journal (data provenance). None until token_changes runs.
LAST_QUOTE_SOURCE: str | None = None


# --------------------------------------------------------------------------- #
# Gating: intel master flag + capability map
# --------------------------------------------------------------------------- #
def _capability() -> dict:
    now = time.time()
    if _cap_cache["map"] is not None and now - _cap_cache["ts"] < 300:
        return _cap_cache["map"]
    try:
        m = json.loads(_CAP_PATH.read_text())
    except Exception:
        m = {}
    _cap_cache.update(ts=now, map=m)
    return m


def _available(path: str) -> bool:
    """True unless the capability map EXPLICITLY marks this endpoint unavailable."""
    rec = _capability().get(path)
    return True if rec is None else bool(rec.get("ok"))


def _get(path: str, params: dict | None = None, **kw):
    """Intel-gated CMC fetch: returns None unless intel is enabled AND the endpoint is
    in-tier per the capability map."""
    if not settings.cmc_intel_enabled or not _available(path):
        return None
    return CMC.get(path, params or {}, **kw)


# --------------------------------------------------------------------------- #
# Fetchers
# --------------------------------------------------------------------------- #
def global_metrics() -> dict | None:
    """Total market cap, BTC/ETH/stablecoin dominance, total volume (latest)."""
    b = _get("/v1/global-metrics/quotes/latest", {}, data_class="global_metrics", est_credits=1)
    if not b:
        return None
    try:
        d = b["data"]
        q = d["quote"]["USD"]
        return {
            "btc_dominance": d.get("btc_dominance"),
            "eth_dominance": d.get("eth_dominance"),
            "stablecoin_market_cap": d.get("stablecoin_market_cap"),
            "total_market_cap": q.get("total_market_cap"),
            "total_volume_24h": q.get("total_volume_24h"),
            "altcoin_market_cap": q.get("altcoin_market_cap"),
        }
    except (KeyError, TypeError):
        return None


def _global_metrics_prev(days: int = 30) -> dict | None:
    """Dominance + total market cap ~`days` ago (the trend baseline)."""
    b = _get(
        "/v1/global-metrics/quotes/historical",
        {"interval": f"{days}d", "count": 2},
        data_class="global_metrics",
        est_credits=1,
        cache_ttl=6 * 3600,
    )
    if not b:
        return None
    try:
        quotes = b["data"]["quotes"]
        oldest = quotes[0]
        return {
            "btc_dominance": oldest.get("btc_dominance"),
            "total_market_cap": oldest["quote"]["USD"].get("total_market_cap"),
        }
    except (KeyError, TypeError, IndexError):
        return None


def global_metrics_history(days: int = 760) -> list[dict]:
    """Full DAILY series of BTC dominance + total market cap, oldest-first:
    [{ts:int(epoch_s), btc_dominance:float, total_market_cap:float}]. For the backtest
    A/B (a long macro series, unlike `_global_metrics_prev`'s single 30d-ago point).
    ~ceil(days/100) credits, 6h-cached. [] on failure (never raises)."""
    count = max(2, min(int(days), 760))
    b = _get(
        "/v1/global-metrics/quotes/historical",
        {"interval": "1d", "count": count},
        data_class="global_metrics",
        est_credits=max(1, math.ceil(count / 100)),
        cache_ttl=6 * 3600,
    )
    if not b:
        return []
    try:
        rows = []
        for q in b["data"]["quotes"]:
            rows.append(
                {
                    "ts": int(pd.Timestamp(q["timestamp"]).timestamp()),
                    "btc_dominance": q.get("btc_dominance"),
                    "total_market_cap": q.get("quote", {}).get("USD", {}).get("total_market_cap"),
                }
            )
        rows = [
            r for r in rows if r["btc_dominance"] is not None and r["total_market_cap"] is not None
        ]
        rows.sort(key=lambda r: r["ts"])  # normalise oldest-first (CMC order can vary)
        return rows
    except (KeyError, TypeError, ValueError, IndexError):
        return []


def fng_history(days: int = 14) -> list[dict]:
    """Fear & Greed history, oldest-first: [{ts, value, label}]."""
    b = _get(
        "/v3/fear-and-greed/historical", {"limit": days}, data_class="fear_greed", est_credits=1
    )
    if not b:
        return []
    try:
        out = [
            {
                "ts": int(r["timestamp"]),
                "value": int(r["value"]),
                "label": r.get("value_classification"),
            }
            for r in b["data"]
        ]
        out.sort(key=lambda r: r["ts"])
        return out
    except (KeyError, TypeError, ValueError):
        return []


def build_regime_intel() -> dict | None:
    """Inputs for the enhanced regime model: BTC-dominance + total-mktcap NOW vs ~30d
    ago, and F&G now vs its 7-day average. Returns a plain dict (None if intel disabled
    or every field failed). Phase 3 maps this to a RegimeIntel dataclass."""
    if not settings.cmc_intel_enabled:
        return None
    gm = global_metrics() or {}
    prev = _global_metrics_prev(30) or {}
    fh = fng_history(14)
    fng_now = fh[-1]["value"] if fh else None
    last7 = [r["value"] for r in fh[-7:]]
    fng_7d_avg = round(sum(last7) / len(last7), 2) if last7 else None
    out = {
        "btc_dominance": gm.get("btc_dominance"),
        "btc_dominance_prev": prev.get("btc_dominance"),
        "total_mktcap": gm.get("total_market_cap"),
        "total_mktcap_prev": prev.get("total_market_cap"),
        "fng_now": fng_now,
        "fng_7d_avg": fng_7d_avg,
    }
    return None if all(v is None for v in out.values()) else out


def token_changes(symbols, *, prefer_snapshot: bool = True, max_age_s: int = 300) -> dict:
    """{SYM: {pct_24h, pct_7d}} for the universe tilt.

    Prefers the local CMC-WS quote snapshot (`cmc_stream_store.quote_snapshot`) — 0 credits, 0
    network, firewall-safe — when ≥2 tokens carry a fresh `pct_7d` (all `momentum_tilt` needs to
    act). Otherwise falls back to ONE batched `quotes/latest` REST call (the original path). The
    snapshot's `pct_7d`/`pct_24h` ARE CMC's `percent_change_7d`/`percent_change_24h` (same source,
    streamed) — verified against the live full-mode frame."""
    global LAST_QUOTE_SOURCE
    if prefer_snapshot:
        try:
            from ictbot.data import cmc_stream_store

            snap = cmc_stream_store.quote_snapshot(max_age_s)
            out = {
                s: {"pct_24h": snap[s].get("pct_24h"), "pct_7d": snap[s].get("pct_7d")}
                for s in symbols
                if s in snap and snap[s].get("pct_7d") is not None
            }
            if len(out) >= 2:
                LAST_QUOTE_SOURCE = "cmc_ws"
                return out
        except Exception:  # noqa: BLE001 — local read must never break the tick
            pass
    LAST_QUOTE_SOURCE = "rest"
    b = _get(
        "/v2/cryptocurrency/quotes/latest",
        {"symbol": ",".join(symbols)},
        data_class="quotes",
        est_credits=1,
    )
    if not b:
        return {}
    out: dict = {}
    try:
        d = b["data"]
        for sym in symbols:
            entry = d.get(sym)
            if isinstance(entry, list):
                entry = entry[0] if entry else None
            if not entry:
                continue
            q = entry.get("quote", {}).get("USD", {})
            out[sym] = {
                "pct_24h": q.get("percent_change_24h"),
                "pct_7d": q.get("percent_change_7d"),
            }
    except (KeyError, TypeError):
        pass
    return out


def _split_movers(items: list[dict], top: int = 5) -> dict:
    """Split a [{symbol,name,pct_24h}] list into sign-correct gainers/losers. A 'loser' is
    strictly pct<0 — never the smallest gainer (the bug when a list skews all-positive)."""
    items = [i for i in items if i.get("pct_24h") is not None]
    gainers = sorted(
        [i for i in items if i["pct_24h"] > 0], key=lambda i: i["pct_24h"], reverse=True
    )[:top]
    losers = sorted([i for i in items if i["pct_24h"] < 0], key=lambda i: i["pct_24h"])[:top]
    return {"gainers": gainers, "losers": losers}


def gainers_losers(limit: int = 10) -> dict:
    """Trending movers fallback: {gainers, losers} from CMC's trending endpoint, split by
    sign. NOTE: trending surfaces volatile micro-caps — prefer `market_movers` (top-mcap)."""
    b = _get(
        "/v1/cryptocurrency/trending/gainers-losers",
        {"limit": limit, "time_period": "24h"},
        data_class="trending",
        est_credits=1,
    )
    if not b:
        return {"gainers": [], "losers": []}
    try:
        items = [
            {
                "symbol": r.get("symbol"),
                "name": r.get("name"),
                "pct_24h": r.get("quote", {}).get("USD", {}).get("percent_change_24h"),
            }
            for r in b["data"]
        ]
        return _split_movers(items, 5)
    except (KeyError, TypeError):
        return {"gainers": [], "losers": []}


def market_movers(limit: int = 100, top: int = 5) -> dict:
    """The dashboard's movers: biggest 24h gainers/losers among the top-`limit` coins by
    MARKET CAP (listings/latest), split strictly by sign. Top-mcap is the sane universe —
    real majors with correct signs, not the micro-cap pumps CMC's trending endpoint surfaces
    (which also produced positive 'losers'). Falls back to trending if listings is
    unavailable. {gainers: [...], losers: [...]}; never raises."""
    b = _get(
        "/v1/cryptocurrency/listings/latest",
        {"start": 1, "limit": limit, "sort": "market_cap", "sort_dir": "desc"},
        data_class="listings",
        est_credits=max(1, math.ceil(limit / 200)),
    )
    if not b:
        return gainers_losers(top * 2)
    try:
        items = [
            {
                "symbol": r.get("symbol"),
                "name": r.get("name"),
                "pct_24h": r.get("quote", {}).get("USD", {}).get("percent_change_24h"),
            }
            for r in b["data"]
        ]
        return _split_movers(items, top)
    except (KeyError, TypeError):
        return {"gainers": [], "losers": []}


def categories(limit: int = 8) -> list[dict]:
    """Top crypto categories by average price change (sector rotation intel)."""
    b = _get(
        "/v1/cryptocurrency/categories", {"limit": limit}, data_class="categories", est_credits=1
    )
    if not b:
        return []
    try:
        out = [
            {
                "name": r.get("name"),
                "avg_price_change": r.get("avg_price_change"),
                "market_cap": r.get("market_cap"),
                "market_cap_change": r.get("market_cap_change"),
                "num_tokens": r.get("num_tokens"),
            }
            for r in b["data"]
        ]
        out.sort(key=lambda r: r["avg_price_change"] or 0, reverse=True)
        return out
    except (KeyError, TypeError):
        return []


def daily_ohlcv(symbol: str, days: int = 730) -> pd.DataFrame | None:
    """DAILY OHLCV for `symbol` as the standard [time,o,h,l,c,v] frame — a long-window regime input
    and the cold-start seed source. None on failure.

    FREE_DATA (default): free, keyless Binance daily klines (CMC's historical OHLCV endpoint is gone).
    Binance serves up to 1000 daily bars/req (~2.7y), covering the 730-day window we use."""
    if settings.free_data and not settings.cmc_only:
        from ictbot.data.freefeeds import binance_klines

        df = binance_klines(symbol, interval="1d", limit=min(int(days), 1000))
        if df is not None and len(df):
            return df
    count = max(1, min(int(days), 1095))
    b = _get(
        "/v2/cryptocurrency/ohlcv/historical",
        {"symbol": symbol, "interval": "daily", "count": count},
        data_class="daily_ohlcv",
        est_credits=max(1, math.ceil(count / 100)),
    )
    if not b:
        return None
    try:
        d = b["data"]
        node = d.get(symbol)
        if node is None:
            node = next(iter(d.values()))
        quotes = node["quotes"] if isinstance(node, dict) else node[0]["quotes"]
        recs = []
        for q in quotes:
            u = q["quote"]["USD"]
            t = q.get("time_open") or u.get("timestamp")
            recs.append(
                {
                    "time": pd.Timestamp(t).tz_localize(None)
                    if pd.Timestamp(t).tzinfo
                    else pd.Timestamp(t),
                    "open": float(u["open"]),
                    "high": float(u["high"]),
                    "low": float(u["low"]),
                    "close": float(u["close"]),
                    "volume": float(u.get("volume") or 0.0),
                }
            )
        if not recs:
            return None
        return pd.DataFrame(recs).sort_values("time").reset_index(drop=True)
    except (KeyError, TypeError, IndexError, ValueError):
        return None


def market_intel_snapshot() -> dict | None:
    """Everything the dashboard Market-Intelligence panel renders. None if intel
    disabled. Each piece degrades independently (missing piece → None/[])."""
    if not settings.cmc_intel_enabled:
        return None
    fh = fng_history(14)
    return {
        "global": global_metrics(),
        "fng_trend": [{"ts": r["ts"], "value": r["value"]} for r in fh],
        "movers": market_movers(100, 5),
        "categories": categories(6),
    }
