"""
FREE, keyless market overview — the CoinMarketCap Agent-Hub `market_overview()` replacement.

CMC's MCP "composed market-overview skill" is gone (returns None under free data), which stripped the
sold report's macro headline. This rebuilds the SAME shape from free public APIs so consumers
(`agent/regime_report.py`, the dashboard, the ERC-8183 preview) need no change:

  fear_greed        <- alternative.me            (freefeeds.alt_me_fear_greed)
  btc_dominance     <- CoinGecko /global         (freefeeds.coingecko_global)
  mktcap_change_24h <- CoinGecko /global
  regime/risk_budget<- derived from F&G + universe breadth (DexScreener 24h changes)
  narratives        <- the universe's top 24h movers (DexScreener)
  ta_breadth/health <- share of the universe above its rolling MA (the free Binance close matrix)

Best-effort + never-raise: any source can miss and the field just degrades to None. Keeps the exact
field NAMES `cmc_agent_hub.market_overview()` produced so nothing downstream changes.
"""

from __future__ import annotations

from typing import Any

FREE_TOOLS = ["alternative.me/fng", "coingecko/global", "dexscreener/tokens"]


def _risk_budget(fear_greed: int | None, share_up: float | None) -> float | None:
    """Blend sentiment (F&G, 0..100) with breadth (share of the universe up on 24h) into a 0..1 risk
    budget — the same [0,1] scale the old CMC `_ta_risk_budget` produced. None if both inputs miss."""
    parts = []
    if fear_greed is not None:
        parts.append(fear_greed / 100.0)
    if share_up is not None:
        parts.append(share_up)
    if not parts:
        return None
    return round(sum(parts) / len(parts), 4)


def _regime(risk_budget: float | None) -> str | None:
    """Map the risk budget to the same labels CMC used (≥0.6 risk-on, ≤0.4 risk-off, else neutral)."""
    if risk_budget is None:
        return None
    if risk_budget >= 0.6:
        return "risk-on"
    if risk_budget <= 0.4:
        return "risk-off"
    return "neutral"


def free_ta_health() -> float | None:
    """FREE replacement for `cmc_agent_hub.basket_ta_health()` — the share (0..1) of the contest
    universe trading above its rolling mean on the free Binance 4h close matrix. None on any miss."""
    try:
        from ictbot.data.cmc import cmc_4h_close_matrix
        from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

        df = cmc_4h_close_matrix(CONTEST_TOKENS)
        if df is None or getattr(df, "empty", True) or df.shape[0] < 50:
            return None
        window = min(120, df.shape[0])
        ma = df.tail(window).mean()
        last = df.iloc[-1]
        above = sum(1 for c in df.columns if last[c] > ma[c])
        return round(above / len(df.columns), 4)
    except Exception:
        return None


def free_market_overview() -> dict[str, Any] | None:
    """Build the market-overview dict from free sources, mirroring `cmc_agent_hub.market_overview()`.
    Returns None only if NOTHING resolved (so the report can fall through to its degraded state)."""
    try:
        from ictbot.data import dexscreener, freefeeds
        from ictbot.strategy.momentum_allocator import CONTEST_TOKENS
    except Exception:
        return None

    fg = freefeeds.alt_me_fear_greed()
    glob = freefeeds.coingecko_global() or {}
    sigs = dexscreener.dex_signals(list(CONTEST_TOKENS)) or {}

    changes = {s: d.get("price_change_h24") for s, d in sigs.items() if d.get("price_change_h24") is not None}
    share_up = (sum(1 for v in changes.values() if v > 0) / len(changes)) if changes else None
    risk_budget = _risk_budget(fg, share_up)
    regime = _regime(risk_budget)
    # "narratives" = the universe's top 24h movers (best free stand-in for CMC's trending narratives).
    narratives = [s for s, _ in sorted(changes.items(), key=lambda kv: kv[1], reverse=True)[:3]] or None
    ta_health = free_ta_health()

    # Nothing resolved at all → let the caller degrade rather than ship an empty overview.
    if fg is None and not glob and not sigs:
        return None

    return {
        "skill_source": "free-apis:composed-market-overview",
        "risk_budget": risk_budget,
        "regime": regime,
        "fear_greed": fg,
        "btc_dominance": glob.get("btc_dominance"),
        "mktcap_change_24h": glob.get("mktcap_change_24h"),
        "ta_breadth": {"tokens": len(CONTEST_TOKENS), "up_24h": (int(share_up * len(changes)) if share_up is not None else None), "ta_health": ta_health},
        "headline": "Free composed market-overview: Binance + alternative.me + CoinGecko + DexScreener",
        "narratives": narratives,
        "tools_used": FREE_TOOLS,
        "notes": "CMC-free: sentiment (alternative.me), macro (CoinGecko), DEX breadth (DexScreener)",
    }
