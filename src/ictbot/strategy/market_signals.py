"""
Unified per-token signal buffet — the single place strategies/levers read CMC-derived signals from.

Merges every per-token signal the WS harvest + CEX snapshot expose into one
`token_signals(symbols) -> {SYM: {...}}`:
  - **momentum**: `pct_24h/pct_7d/pct_30d` (CMC-native multi-window % change, CEX snapshot),
  - **flow**: `flow_ratio` (on-chain buy/(buy+sell)), `unique_traders`,
  - **liquidity**: `liquidity_usd` (token_agg `lu`, all BSC tokens), `volume_24h` (CEX),
  - **risk**: `top10_pct` (holder concentration), `net_liquidity_usd` (DEX add−remove, 1h),
    `whale_net_usd` (net large-swap flow, 1h).

Each lever reads the fields it needs (see the strategy↔data matrix in docs/cmc_candles.md). Every
field is independently optional (a channel may be stale while another is fresh). **Zero-network**
(reads only the local stores → no per-tick credit cost), **never-raise**, staleness-gated. Extends
`strategy/onchain_signals.py` (which stays for the on-chain-only journal payload).
"""

from __future__ import annotations

from ictbot.data import cmc_stream_store as store
from ictbot.settings import settings
from ictbot.strategy.onchain_signals import flow_ratio


def _dex_token_signals(symbols) -> dict:
    """FREE_DATA: build the token_signals dict from DexScreener instead of the (dead) CMC WebSocket
    stores. Keeps EVERY key so the overlays / `mom_blend` / the report's `_cmc_signals` are unchanged.
    DexScreener has no holder/whale data, so `top10_pct`, `top100_pct`, `net_liquidity_usd`,
    `whale_net_usd`, `unique_traders`, and multi-window `pct_7d/30d` are `None` (all already tolerated).
    `flow_ratio` is derived from the 24h buy/sell tx counts."""
    from ictbot.data import dexscreener, freefeeds

    sigs = dexscreener.dex_signals(list(symbols))
    out: dict = {}
    for s in symbols:
        d = sigs.get(s)
        if not d:
            continue
        buys, sells = d.get("buys_24h"), d.get("sells_24h")
        fr = None
        if buys is not None and sells is not None and (buys + sells) > 0:
            fr = round(buys / (buys + sells), 4)  # buy-share of DEX txns, ~ the CMC flow_ratio
        # DexScreener only exposes 24h change; 7d/30d come free from Binance daily klines so the
        # allocator's `mom_blend` stays a true multi-window read (not 24h-only).
        win = freefeeds.window_returns(s)
        rec = {
            "pct_24h": d.get("price_change_h24"),
            "pct_7d": win.get("pct_7d"),
            "pct_30d": win.get("pct_30d"),
            "volume_24h": d.get("volume_24h"),
            "market_cap": d.get("market_cap"),
            "flow_ratio": fr,
            "unique_traders": None,
            "liquidity_usd": d.get("liquidity_usd"),
            "top10_pct": None,
            "top100_pct": None,
            "net_liquidity_usd": None,
            "whale_net_usd": None,
        }
        if any(v is not None for v in rec.values()):
            out[s] = rec
    return out


def token_signals(symbols, *, window: str = "24h") -> dict:
    """`{SYM: {pct_24h, pct_7d, pct_30d, volume_24h, market_cap, flow_ratio, unique_traders,
    liquidity_usd, top10_pct, top100_pct, net_liquidity_usd, whale_net_usd}}` for `symbols`.
    Only tokens with at least one fresh signal appear; every field is optional. Zero-network.

    FREE_DATA (default): sourced from DexScreener (`_dex_token_signals`) — the CMC on-chain stream is
    gone. The legacy CMC-store path below runs only when free data is off."""
    if settings.free_data:
        return _dex_token_signals(symbols)
    quotes = store.quote_snapshot()                 # CEX: price, mc, vol, pct_*
    metrics = store.onchain_token_metrics()         # on-chain flow per window
    holders = store.onchain_holders()               # concentration
    agg = store.onchain_token_liquidity()           # token liquidity (lu)
    whale = store.onchain_whale_flow()              # whale net flow

    out: dict = {}
    for s in symbols:
        q = quotes.get(s) or {}
        m = metrics.get(s) or {}
        h = holders.get(s) or {}
        w = (m.get("windows") or {}).get(window) or {}
        rec = {
            "pct_24h": q.get("pct_24h"),
            "pct_7d": q.get("pct_7d"),
            "pct_30d": q.get("pct_30d"),
            "volume_24h": q.get("volume_24h"),
            "market_cap": q.get("market_cap"),
            "flow_ratio": flow_ratio(m, window),
            "unique_traders": w.get("unique_traders"),
            "liquidity_usd": (agg.get(s) or {}).get("liquidity_usd"),
            "top10_pct": h.get("top10_pct"),
            "top100_pct": h.get("top100_pct"),
            "net_liquidity_usd": store.onchain_net_liquidity_usd(s),  # store reader is never-raising
            "whale_net_usd": (whale.get(s) or {}).get("whale_net_usd"),
        }
        if any(v is not None for v in rec.values()):
            out[s] = rec
    return out


def mom_blend(sig: dict, *, w24: float = 0.5, w7: float = 0.35, w30: float = 0.15) -> float | None:
    """A single CMC-native multi-window momentum number for a token from its signal record:
    weighted blend of `pct_24h/7d/30d` (whichever are present, re-normalized). None if no window.
    The allocator cross-sectionally ranks these, so absolute scale doesn't matter."""
    terms = [(sig.get("pct_24h"), w24), (sig.get("pct_7d"), w7), (sig.get("pct_30d"), w30)]
    terms = [(v, wt) for v, wt in terms if isinstance(v, (int, float)) and wt > 0]
    if not terms:
        return None
    return sum(v * wt for v, wt in terms) / sum(wt for _, wt in terms)
