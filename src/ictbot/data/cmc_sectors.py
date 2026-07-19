"""
CMC sector/narrative map — which CMC categories each contest token belongs to, so the allocator can
rotate the held book toward CMC's *live* `trending_crypto_narratives` (see `cmc_agent_hub.trending_
narratives()`). This is the membership side of the `sector_tilt` overlay in `strategy/universe_overlay.py`.

Names are CMC-NATIVE category strings so they string-match the trending list verbatim (the tilt lower-
cases both sides). CMC's own taxonomy includes "Binance Ecosystem" (== BNB Chain) and exchange-ecosystem
tags — kept here for matching; ANY dashboard surfacing MUST pass them through the `cmcLabel()` sanitizer
(no raw "Binance" in the frontend). Verified against `/v2/cryptocurrency/info` categories for coin_id 1839
(BSC); refresh with `derive_sectors()`.
"""

from __future__ import annotations

from ictbot.data.cmc_agent_hub import CMC_IDS

# SYM -> set of CMC category/narrative names the token is a member of. Multi-tag on purpose: a token
# matches the trending list if ANY of its categories is trending. Kept small + high-signal (the broad
# "Binance Ecosystem"/"Layer 1" tags + the token's defining narrative).
TOKEN_SECTORS: dict[str, set[str]] = {
    "BNB":  {"Binance Ecosystem", "BNB Chain", "Layer 1", "Centralized Exchange (CEX) Token"},
    "ETH":  {"Layer 1", "Smart Contracts", "Ethereum Ecosystem"},
    "CAKE": {"DeFi", "Decentralized Exchange (DEX) Token", "Binance Ecosystem", "Yield Farming"},
    "LINK": {"Oracles", "DeFi", "Ethereum Ecosystem"},
    "UNI":  {"DeFi", "Decentralized Exchange (DEX) Token", "Ethereum Ecosystem", "Governance"},
    "AVAX": {"Layer 1", "Smart Contracts", "Avalanche Ecosystem"},
    "DOT":  {"Layer 1", "Interoperability", "Polkadot Ecosystem"},
    "DOGE": {"Memes"},
}


def sectors_for(symbol: str) -> set[str]:
    """CMC categories for `symbol` (empty set if unknown). Never raises."""
    return set(TOKEN_SECTORS.get(str(symbol).upper(), set()))


def trending_hits(symbol: str, trending) -> bool:
    """True iff any of `symbol`'s CMC categories is in the live `trending` narrative list
    (case-insensitive). Never raises."""
    if not trending:
        return False
    trend = {str(t).strip().lower() for t in trending if t}
    return any(s.strip().lower() in trend for s in sectors_for(symbol))


def derive_sectors(symbols=tuple(CMC_IDS)) -> dict[str, set[str]]:
    """Refresh the map live from CMC `/v2/cryptocurrency/info` `category`/`tags` (keyed by CMC ID).
    Best-effort + never-raise: returns the static `TOKEN_SECTORS` for any symbol the API can't enrich,
    so a credit-exhausted or MCP-off run still yields a usable map. Intended as an offline updater, not
    a per-tick call."""
    out: dict[str, set[str]] = {s: set(TOKEN_SECTORS.get(s, set())) for s in symbols}
    try:
        from ictbot.data import cmc_agent_hub

        for s in symbols:
            info = cmc_agent_hub.crypto_info(s) if hasattr(cmc_agent_hub, "crypto_info") else None
            cats = (info or {}).get("category") if isinstance(info, dict) else None
            tags = (info or {}).get("tags") if isinstance(info, dict) else None
            merged = {c for c in ([cats] if isinstance(cats, str) else (cats or [])) if c}
            merged |= {t for t in (tags or []) if isinstance(t, str)}
            if merged:
                out[s] = out.get(s, set()) | merged
    except Exception:
        pass
    return out
