"""
Read layer over the CMC **WebSocket-harvested** data store (written by `scripts/cmc_stream.py`).

The streamer subscribes to `market@crypto_latest_price` (full mode) and now harvests the FULL
per-token quote (price, market cap, circulating supply, 24h volume, and the percent-change window
family) into `data/cache/cmc_ws/quotes.json`, alongside the 4h candle cache. This module is the
read side: strategies, the dashboard intel panel, and `cmc_intel.token_changes` read these
local-file signals instead of spending REST credits.

Every reader is:
  - **zero network** — pure local-file read; firewall-safe (`CMC_ONLY` is untouched),
  - **never-raise** — returns `{}` / `None` on missing / stale / parse-error so the caller falls back,
  - **staleness-gated** — records older than `max_age_s` are dropped (the stream may be down).

It also exposes the Phase-0 channel-capability map (`data/journal/cmc_ws_capability.json`) via
`channel_ok()`, mirroring `cmc_intel._available()` so the daemon never subscribes to a channel the
discovery probe found out-of-tier.
"""

from __future__ import annotations

import json
import time

from ictbot.settings import CMC_WS_DIR, JOURNAL_DIR

_WS_DIR = CMC_WS_DIR  # shared across isolated sim tracks (see settings.CMC_WS_DIR)
_QUOTES_PATH = _WS_DIR / "quotes.json"
_ONCHAIN_METRIC_PATH = _WS_DIR / "onchain_token_metric.json"
_ONCHAIN_HOLDERS_PATH = _WS_DIR / "onchain_holders.json"
_ONCHAIN_LIQ_PATH = _WS_DIR / "onchain_liquidity.json"
_ONCHAIN_AGG_PATH = _WS_DIR / "onchain_token_agg.json"
_ONCHAIN_WHALE_PATH = _WS_DIR / "onchain_whale.json"
_CAP_PATH = JOURNAL_DIR / "cmc_ws_capability.json"
_DEFAULT_MAX_AGE_S = 300  # 5 min: REST quotes/latest is ~minute-fresh, so this is an equal/fresher source
_ONCHAIN_MAX_AGE_S = 3600  # on-chain metrics move slower (and liquidity events are sparse)

_cap_cache: dict = {"ts": 0.0, "map": None}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_token_store(path, max_age_s: int) -> dict:
    """Generic staleness-gated reader for the `{updated_ms, tokens:{SYM:{ts,...}}}` snapshot
    files. Returns `{SYM: rec}` for tokens fresher than `max_age_s`; `{}` on missing/stale/error."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        tokens = raw.get("tokens") or {}
    except Exception:
        return {}
    cutoff = _now_ms() - max_age_s * 1000
    return {
        sym: rec
        for sym, rec in tokens.items()
        if isinstance(rec, dict) and isinstance(rec.get("ts"), (int, float)) and rec["ts"] >= cutoff
    }


# --------------------------------------------------------------------------- #
# CEX quote snapshot (Phase 1)
# --------------------------------------------------------------------------- #
def quote_snapshot(max_age_s: int = _DEFAULT_MAX_AGE_S) -> dict:
    """`{SYM: {ts, price, market_cap, circulating_supply, volume_24h, pct_24h, pct_7d, ...}}`
    for every token whose per-token `ts` is fresher than `max_age_s`. `{}` on missing / stale /
    parse-error. Never raises, never touches the network."""
    return _read_token_store(_QUOTES_PATH, max_age_s)


# --------------------------------------------------------------------------- #
# On-chain (DEX) snapshots (Phase 2) — subset CAKE/LINK/UNI, harvested by OnchainWriter.
# --------------------------------------------------------------------------- #
def onchain_token_metrics(max_age_s: int = _ONCHAIN_MAX_AGE_S) -> dict:
    """`{SYM: {ts, price, windows:{<win>:{vol_usd, buys, sells, buy_vol_usd, sell_vol_usd,
    unique_traders, ...}}}}` — on-chain flow per token. `{}` on missing/stale."""
    return _read_token_store(_ONCHAIN_METRIC_PATH, max_age_s)


def onchain_holders(max_age_s: int = _ONCHAIN_MAX_AGE_S) -> dict:
    """`{SYM: {ts, top10_pct, top50_pct, top100_pct, top100_balance}}` — holder concentration."""
    return _read_token_store(_ONCHAIN_HOLDERS_PATH, max_age_s)


def onchain_liquidity(max_age_s: int = _ONCHAIN_MAX_AGE_S) -> dict:
    """`{SYM: {ts, events:[{ts, type, value_usd, pair, tx}]}}` — recent liquidity add/remove."""
    return _read_token_store(_ONCHAIN_LIQ_PATH, max_age_s)


def onchain_token_liquidity(max_age_s: int = _ONCHAIN_MAX_AGE_S) -> dict:
    """`{SYM: {ts, price, liquidity_usd}}` — total token liquidity (`lu`) from token_agg_event,
    available for all BSC universe tokens (no pool discovery). `{}` on missing/stale."""
    return _read_token_store(_ONCHAIN_AGG_PATH, max_age_s)


def onchain_whale_flow(max_age_s: int = _ONCHAIN_MAX_AGE_S) -> dict:
    """`{SYM: {ts, whale_net_usd, whale_count, window_s}}` — rolling net of large swaps
    (buys positive, sells negative) over the last hour. Negative = whales net selling. `{}` stale."""
    return _read_token_store(_ONCHAIN_WHALE_PATH, max_age_s)


def onchain_net_liquidity_usd(sym: str, window_s: int = 3600, max_age_s: int = _ONCHAIN_MAX_AGE_S) -> float | None:
    """Net liquidity flow (USD) for `sym` over the last `window_s` — adds positive, removes
    negative. None if no fresh data. A negative value = net liquidity LEAVING (a risk signal)."""
    rec = onchain_liquidity(max_age_s).get(sym)
    if not rec or not isinstance(rec.get("events"), list):
        return None
    cutoff = _now_ms() - window_s * 1000
    net = 0.0
    seen = False
    for e in rec["events"]:
        if not isinstance(e, dict) or not isinstance(e.get("ts"), (int, float)) or e["ts"] < cutoff:
            continue
        v = e.get("value_usd") or 0.0
        net += v if e.get("type") == "add" else (-v if e.get("type") == "remove" else 0.0)
        seen = True
    return net if seen else None


def quote_age_s() -> float | None:
    """Seconds since the freshest token in the snapshot, or None if missing/unreadable."""
    try:
        raw = json.loads(_QUOTES_PATH.read_text(encoding="utf-8"))
        updated = int(raw.get("updated_ms") or 0)
    except Exception:
        return None
    return None if updated <= 0 else max(0.0, (_now_ms() - updated) / 1000.0)


# --------------------------------------------------------------------------- #
# Channel capability gate (Phase 0 → gates Phase 2 subscribes)
# --------------------------------------------------------------------------- #
def _capability() -> dict:
    now = time.time()
    if _cap_cache["map"] is not None and now - _cap_cache["ts"] < 300:
        return _cap_cache["map"]
    try:
        m = json.loads(_CAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        m = {}
    _cap_cache.update(ts=now, map=m)
    return m


def channel_ok(name: str) -> bool:
    """True unless the discovery probe EXPLICITLY marked channel `name` out-of-tier (subscribe
    failed). Default True for unprobed channels — mirrors `cmc_intel._available`'s optimistic
    default so a missing map never silently disables the running CEX feed."""
    rec = _capability().get(name)
    return True if rec is None else bool(rec.get("ok"))
