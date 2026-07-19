"""
CMC on-chain (DEX) support — BNB-chain address map, DEX-REST pool discovery/quotes, and the pure
frame parsers for the WebSocket `onchain@*` family the streamer ingests (`scripts/cmc_stream.py`).

The contest universe is the fixed 149 **BEP-20 tokens on BNB Chain**, so all on-chain data uses
`platform_id=14` (BNB Smart Chain) BEP-20 contracts. The Startup tier delivers (all verified live):
  - WS `onchain@token_metric` (buy/sell vol, unique traders, txns, h/l per window),
    `onchain@holders_metrics` (top-N concentration), `onchain@liquidity_event` (add/remove USD),
    `onchain@token_agg_event` (aggregated price + **total token liquidity `lu`**),
    `onchain@transaction` (per-swap → whale/large-trade flow). Dropped/skip: `onchain@pool_metric`
    (per-pool unique traders — streamed but NO strategy consumer, so removed as dead weight),
    `onchain@kline` (no data even @469M-vol), `onchain@unique_trader` (redundant with token_metric).
  - REST `/v4/dex/spot-pairs/latest` (pool discovery: `network_id=14` + `dex_slug=pancakeswap-v2` +
    `base_asset_symbol` + `sort=liquidity`) and `/v4/dex/pairs/quotes/latest` (per-pool liquidity
    depth + 24h volume). The `ohlcv`/`trade`/`networks`/`listings` DEX endpoints 500 on this tier.

Addresses are CMC-NATIVE (confirmed via `/v2/cryptocurrency/info`, coin_id 1839 = BSC).
Parsers are PURE (no I/O) so they unit-test against captured real frames; every one is defensive
(`.get`, type-checked) and returns `None`/`{}` on a shape it doesn't recognize.
"""

from __future__ import annotations

import json
import math
import time

from ictbot.data.cmc_agent_hub import CMC_IDS
from ictbot.settings import JOURNAL_DIR, settings

# CMC platform id for the onchain@* channels: BNB Smart Chain.
PLATFORM_BSC = 14

# Universe tokens → BNB-chain (BEP-20) contracts. Verified via /v2/cryptocurrency/info (coin_id
# 1839). BNB is the native gas token → use WBNB for on-chain ops. All on platform_id=14 so the WS
# token-keyed channels (token_metric/holders/liquidity/token_agg/transaction) reflect BNB-chain
# activity for the whole universe (refresh with derive_addresses()).
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
ONCHAIN_TOKENS: dict[str, dict] = {
    "BNB": {"platform_id": PLATFORM_BSC, "address": WBNB},
    "ETH": {"platform_id": PLATFORM_BSC, "address": "0x2170ed0880ac9a755fd29b2688956bd959f933f8"},
    "CAKE": {"platform_id": PLATFORM_BSC, "address": "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"},
    "LINK": {"platform_id": PLATFORM_BSC, "address": "0xf8a0bf9cf54bb92f17374d9e9a321e6a111a51bd"},
    "UNI": {"platform_id": PLATFORM_BSC, "address": "0xbf5140a22578168fd562dccf235e5d43a02ce9b1"},
    "AVAX": {"platform_id": PLATFORM_BSC, "address": "0x1ce0c2827e2ef14d5c4f29a091d735a204794041"},
    "DOT": {"platform_id": PLATFORM_BSC, "address": "0x7083609fce4d1d8dc0c979aab8c869ea2c873402"},
    "DOGE": {"platform_id": PLATFORM_BSC, "address": "0xba2ae424d960c26247dd6c32edc70b295c744c43"},
}

# DEX REST venue per network: the working dex_slug. NOTE V2-only — `pancakeswap-v3` doesn't resolve
# on this key, so tokens whose liquidity is V3-only may not surface a pool (handled gracefully).
DEX_VENUE = {PLATFORM_BSC: "pancakeswap-v2"}
# Quote assets we accept as the pool's quote side, best-first (stables + majors).
_MAJOR_QUOTES = ("USDT", "USDC", "WBNB", "BUSD", "FDUSD", "BNB", "WETH")

_ADDR_CACHE = JOURNAL_DIR / "cmc_onchain_addresses.json"
_POOLS_CACHE = JOURNAL_DIR / "cmc_dex_pools.json"


def onchain_tokens() -> dict[str, dict]:
    """`{SYM: {platform_id, address}}` for the universe — disk-cached override (from
    `derive_addresses()`) merged over the verified built-in map. Never raises.

    The universe is BNB-chain-only by invariant, so a cached override is honored ONLY when it is on
    `platform_id == PLATFORM_BSC` (14). This makes the map self-healing: a stale/wrong cache (e.g. an
    early derive that recorded LINK/UNI at their Ethereum `platform_id=1` contracts) can never poison
    the `onchain@*` subscription addresses — those tokens fall back to the verified built-in BEP-20."""
    out = dict(ONCHAIN_TOKENS)
    try:
        cached = json.loads(_ADDR_CACHE.read_text(encoding="utf-8"))
        for sym, rec in (cached.get("tokens") or {}).items():
            if (isinstance(rec, dict) and rec.get("address")
                    and int(rec.get("platform_id") or 0) == PLATFORM_BSC):
                out[sym] = {"platform_id": PLATFORM_BSC, "address": str(rec["address"]).lower()}
    except Exception:
        pass
    return {s: out[s] for s in out if s in CMC_IDS}  # only contest-universe tokens


def derive_addresses() -> dict[str, dict]:
    """Re-derive the universe's BNB-chain (BEP-20) contracts from CMC's `/v2/cryptocurrency/info`
    (one in-tier call) and cache to `cmc_onchain_addresses.json`. Returns the derived map; {} on
    failure (callers fall back to the built-in `ONCHAIN_TOKENS`). Never raises."""
    syms = [s for s in CMC_IDS if s != "BNB"]  # BNB stays WBNB (native gas has no BEP-20)
    try:
        from ictbot.data.cmc_client import CMC

        b = CMC.get(
            "/v2/cryptocurrency/info", {"symbol": ",".join(syms)}, est_credits=1, data_class="info"
        )
        data = (b or {}).get("data") or {}
    except Exception:
        return {}
    derived: dict[str, dict] = {}
    for sym in syms:
        node = data.get(sym)
        node = node[0] if isinstance(node, list) and node else node
        for c in (node or {}).get("contract_address") or []:
            coin = (((c.get("platform") or {}).get("coin")) or {}).get("id")
            if str(coin) == "1839" and c.get("contract_address"):  # BNB Smart Chain
                derived[sym] = {"platform_id": PLATFORM_BSC, "address": str(c["contract_address"]).lower()}
                break
    if derived:
        try:
            _ADDR_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _ADDR_CACHE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"updated_ms": int(time.time() * 1000), "tokens": derived}, indent=2), encoding="utf-8")
            tmp.replace(_ADDR_CACHE)
        except Exception:
            pass
    return derived


# --------------------------------------------------------------------------- #
# DEX REST — pool discovery + per-pool quotes (liquidity depth + 24h volume)
# --------------------------------------------------------------------------- #
def _quote0(row: dict) -> dict:
    q = row.get("quote") if isinstance(row, dict) else None
    return (q[0] if isinstance(q, list) and q else (q if isinstance(q, dict) else {})) or {}


def derive_pools(symbols=None) -> dict[str, dict]:
    """Discover each token's canonical DEX pool via `/v4/dex/spot-pairs/latest` (highest-liquidity
    pool with a major quote asset) and cache `{sym:{pool_address, pair, liquidity_usd,
    dex_volume_24h}}` to `cmc_dex_pools.json`. Pools are stable → refresh ~daily. {} on failure;
    tokens with no V2 pool are simply omitted. Never raises."""
    from ictbot.data.cmc_client import CMC

    toks = onchain_tokens()
    syms = symbols if symbols is not None else list(toks)
    out: dict[str, dict] = {}
    for sym in syms:
        t = toks.get(sym)
        if not t:
            continue
        slug = DEX_VENUE.get(int(t["platform_id"]))
        if not slug:
            continue
        try:
            b = CMC.get(
                "/v4/dex/spot-pairs/latest",
                {"network_id": t["platform_id"], "dex_slug": slug, "base_asset_symbol": sym,
                 "sort": "liquidity", "limit": 20},
                est_credits=1, data_class="dex_pairs", cache_ttl=12 * 3600,
            )
            rows = (b or {}).get("data") or []
        except Exception:
            rows = []
        addr = t["address"].lower()
        # Prefer rows whose base contract is exactly our token; else fall back to symbol match.
        cand = [r for r in rows if str(r.get("base_asset_contract_address", "")).lower() == addr] \
            or [r for r in rows if str(r.get("base_asset_symbol", "")).upper() == sym]
        # Require a MAJOR-quote pool with real 24h volume (skips dead/wash pools with stale prices
        # like a 0-volume DOGE/BETH pair); pick the deepest by liquidity. No match → omit the token.
        good = [r for r in cand if str(r.get("quote_asset_symbol", "")).upper() in _MAJOR_QUOTES
                and (_quote0(r).get("volume_24h") or 0) > 0]
        r = max(good, key=lambda r: _quote0(r).get("liquidity") or 0.0, default=None)
        if r and r.get("contract_address"):
            q = _quote0(r)
            out[sym] = {"pool_address": str(r["contract_address"]).lower(), "pair": r.get("name"),
                        "dex_slug": slug, "network_id": int(t["platform_id"]),
                        "liquidity_usd": q.get("liquidity"), "dex_volume_24h": q.get("volume_24h")}
    if out:
        try:
            _POOLS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _POOLS_CACHE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"updated_ms": int(time.time() * 1000), "pools": out}, indent=2), encoding="utf-8")
            tmp.replace(_POOLS_CACHE)
        except Exception:
            pass
    return out


def cached_pools() -> dict[str, dict]:
    """The disk-cached `{sym: {pool_address, ...}}` from `derive_pools()`. {} if absent."""
    try:
        return json.loads(_POOLS_CACHE.read_text(encoding="utf-8")).get("pools") or {}
    except Exception:
        return {}


def dex_quotes(symbols=None) -> dict[str, dict]:
    """Live per-pool DEX quote for each cached pool via `/v4/dex/pairs/quotes/latest` →
    `{sym:{price, liquidity_usd, dex_volume_24h, pct_1h, pct_24h, pool_address}}`. Budgeted +
    TTL-cached. {} when no pools cached / on failure. Never raises."""
    from ictbot.data.cmc_client import CMC

    pools = cached_pools()
    syms = symbols if symbols is not None else list(pools)
    out: dict[str, dict] = {}
    for sym in syms:
        p = pools.get(sym)
        if not p or not p.get("pool_address"):
            continue
        try:
            b = CMC.get(
                "/v4/dex/pairs/quotes/latest",
                {"network_id": p.get("network_id", PLATFORM_BSC), "contract_address": p["pool_address"]},
                est_credits=1, data_class="dex_quotes", cache_ttl=300,
            )
            data = (b or {}).get("data")
            row = (data[0] if isinstance(data, list) and data else data) or {}
        except Exception:
            row = {}
        q = _quote0(row)
        if q:
            out[sym] = {"price": q.get("price"), "liquidity_usd": q.get("liquidity"),
                        "dex_volume_24h": q.get("volume_24h"), "pct_1h": q.get("percent_change_price_1h"),
                        "pct_24h": q.get("percent_change_price_24h"), "pool_address": p["pool_address"]}
    return out


# --------------------------------------------------------------------------- #
# Pure WS frame parsers
# --------------------------------------------------------------------------- #
def classify_frame(data: dict) -> str | None:
    """Identify which channel a `data` frame came from by its shape (multiplexed frames carry no
    channel tag). → cex | token_metric | holders | liquidity | token_agg | transaction | None."""
    if not isinstance(data, dict):
        return None
    if "cid" in data:
        return "cex"
    if "sts" in data:
        return "token_metric"
    if "lu" in data and "ap" in data:
        return "token_agg"
    if "tx" in data and data.get("tp") in {"buy", "sell"}:
        return "transaction"
    if "tx" in data and data.get("tp") in {"add", "remove", "migrate"}:
        return "liquidity"
    if any(k in data for k in ("t10p", "t50p", "t100p")):
        return "holders"
    return None


_WINDOW_FIELDS = {
    "vu": "vol_usd", "vn": "vol_native", "pc": "pc", "txs": "txs", "bc": "buys", "sc": "sells",
    "bvu": "buy_vol_usd", "svu": "sell_vol_usd", "ut": "unique_traders", "but": "buy_traders",
    "sut": "sell_traders", "h": "high", "l": "low",
}


def parse_token_metric(data: dict) -> dict | None:
    """`onchain@token_metric` → {price, windows:{<win>:{vol_usd, buys, sells, buy_vol_usd,
    sell_vol_usd, unique_traders, high, low, ...}}}. None if no usable window."""
    if not isinstance(data, dict) or not isinstance(data.get("sts"), list):
        return None
    windows: dict[str, dict] = {}
    for w in data["sts"]:
        if not isinstance(w, dict) or not w.get("win"):
            continue
        rec = {dst: float(w[src]) for src, dst in _WINDOW_FIELDS.items() if isinstance(w.get(src), (int, float))}
        if rec:
            windows[str(w["win"])] = rec
    if not windows:
        return None
    out = {"windows": windows}
    if isinstance(data.get("p"), (int, float)):
        out["price"] = float(data["p"])
    return out


def parse_holders(data: dict) -> dict | None:
    """`onchain@holders_metrics` (tp=top_share) → {top10_pct, top50_pct, top100_pct, top100_balance}."""
    if not isinstance(data, dict):
        return None
    m = {"top10_pct": data.get("t10p"), "top50_pct": data.get("t50p"),
         "top100_pct": data.get("t100p"), "top100_balance": data.get("t100ab")}
    out = {k: float(v) for k, v in m.items() if isinstance(v, (int, float))}
    return out or None


def parse_liquidity_event(data: dict) -> dict | None:
    """`onchain@liquidity_event` → {type: add|remove|migrate, value_usd, pair, tx}."""
    if not isinstance(data, dict) or data.get("tp") not in {"add", "remove", "migrate"}:
        return None
    pair = f"{data.get('t0s') or '?'}/{data.get('t1s') or '?'}" if (data.get("t0s") or data.get("t1s")) else None
    return {"type": data["tp"],
            "value_usd": float(data["vu"]) if isinstance(data.get("vu"), (int, float)) else 0.0,
            "pair": pair, "tx": data.get("tx")}


def parse_token_agg(data: dict) -> dict | None:
    """`onchain@token_agg_event` → {price, liquidity_usd}. `lu` = total token liquidity across pools
    (a token-level liquidity-depth signal, no pool discovery needed). None if no liquidity present."""
    if not isinstance(data, dict) or not isinstance(data.get("lu"), (int, float)):
        return None
    out = {"liquidity_usd": float(data["lu"])}
    if isinstance(data.get("ap"), (int, float)):
        out["price"] = float(data["ap"])
    return out


def parse_transaction(data: dict) -> dict | None:
    """`onchain@transaction` (one swap) → {type: buy|sell, value_usd}. The OnchainWriter aggregates
    these into a rolling whale-flow metric (large swaps only) — we never store the raw firehose."""
    if not isinstance(data, dict) or data.get("tp") not in {"buy", "sell"}:
        return None
    return {"type": data["tp"],
            "value_usd": float(data["vu"]) if isinstance(data.get("vu"), (int, float)) else 0.0}
