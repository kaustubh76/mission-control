"""
The agent's edge, packaged as a sellable deliverable: a live **Market Regime Report**.

The trading agent already computes a market-driven regime read each tick (breadth + trend +
volatility + Fear & Greed → a risk-on score, a deployment cap, and a top-k momentum ranking).
This module composes those EXISTING building blocks into one JSON-serialisable report that the
ERC-8183 commerce layer (`agent/commerce.py`) sells to other agents.

It deliberately reuses the production decision path so the report IS what the agent trades on:
  - `momentum_allocator.AllocatorParams` / the strategy `registry` (the same `target_weights_now`
    dispatch `run_allocator._tick` uses) → weights + regime score + deploy cap
  - `data.cmc.cmc_4h_close_matrix` + `data.cmc.fear_greed` → the market inputs
  - `agent.rationale.explain` → the natural-language rationale
  - `data.cmc_agent_hub.market_overview` (best-effort) → the composed Data-MCP overview

No new analysis — it packages what the agent already knows. Pure-ish: it reads market data but takes
no wallet/chain action, so it is unit-testable by injecting a `close_df`.
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from ictbot.settings import settings
from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS, AllocatorParams

REPORT_SCHEMA = "cmc-regime-report/v1"


def _params() -> AllocatorParams:
    """Mirror of `run_allocator.params()` — the contest-locked allocator params from settings."""
    return AllocatorParams(
        lookback=settings.alloc_lookback,
        top_k=settings.alloc_top_k,
        deploy_cap=settings.alloc_deploy_cap,
        vol_lookback=settings.alloc_vol_lookback,
        rebal_bars=settings.alloc_rebal_bars,
        abs_filter=settings.alloc_abs_filter,
    )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _decision(close_df: pd.DataFrame, fg: int | None):
    """Run the SAME strategy dispatch the live tick uses, with the core (always-on) context.

    The optional enhanced terms (CMC macro `intel`, TA health, per-token TA) are flag-gated and
    default OFF, so omitting them yields the baseline regime read — exactly the validated path
    when those flags are unset. Returns the strategy `WeightDecision` (weights, score, cap)."""
    strat_name = settings.strategy_name or (
        "momentum_adaptive" if settings.alloc_adaptive else "momentum"
    )
    strat = registry.get(strat_name)
    ctx = registry.StratContext(
        params=_params(),
        active=None,
        deploy_cap=settings.alloc_deploy_cap,
        floor=settings.alloc_cap_floor,
        ceiling=settings.alloc_cap_ceiling,
        ma_window=settings.alloc_breadth_ma,
        fear_greed=fg,
    )
    return strat_name, strat.target_weights_now(close_df, ctx=ctx)


# On-chain signal fields safe to ship in the SOLD deliverable — public market data only, never a key,
# wallet password, or path. Mirrors `strategy.market_signals.token_signals`.
_ONCHAIN_PUBLIC = ("flow_ratio", "liquidity_usd", "top10_pct", "whale_net_usd",
                   "net_liquidity_usd", "unique_traders", "volume_24h")


def _cmc_signals() -> dict[str, Any] | None:
    """The agent's CURRENT live CMC tilt — sector rotation (CMC trending narratives), CMC-native
    multi-window momentum, and on-chain (DEX) signals — pulled from the latest journaled tick (the
    agent's REAL decision, not a recompute). This is the richest part of the Agent-Hub view, so it
    makes the sold report a true CMC intelligence product rather than just a regime score + weights.

    Public market data ONLY (whitelisted on-chain fields). Best-effort + never-raise; returns None
    when the rotation levers are off / the journal is empty."""
    try:
        from ictbot.api import reads

        row = reads._latest_rebalance(reads.read_journal()) or {}
        out: dict[str, Any] = {}
        rot = row.get("cmc_rotation")
        if isinstance(rot, dict):
            if rot.get("sector_hits"):
                out["sector_rotation"] = list(rot["sector_hits"])
            if rot.get("trending"):
                out["trending_narratives"] = list(rot["trending"])
            if rot.get("mom"):
                out["cmc_momentum"] = {str(k): v for k, v in rot["mom"].items()}
        onch = row.get("onchain_signals")
        if isinstance(onch, dict) and onch:
            clean: dict[str, Any] = {}
            for sym, sig in onch.items():
                if isinstance(sig, dict):
                    rec = {k: sig[k] for k in _ONCHAIN_PUBLIC if sig.get(k) is not None}
                    if rec:
                        clean[str(sym)] = rec
            if clean:
                out["onchain"] = clean
        return out or None
    except Exception:
        return None


def build_report(close_df: pd.DataFrame | None = None, *, query: str | None = None) -> dict[str, Any]:
    """Build the Market Regime Report deliverable.

    `close_df`: an aligned 4h close matrix over CONTEST_TOKENS. When None, it is fetched live from
    CMC (`cmc_4h_close_matrix`). `query`: optional free-text request from the buyer (echoed back).
    Never raises — on a data miss it returns a well-formed report with `status="degraded"`."""
    fg: int | None = None
    try:
        from ictbot.data.cmc import fear_greed

        fg = fear_greed(settings.cmc_api_key or None)
    except Exception:
        fg = None

    if close_df is None:
        try:
            from ictbot.data.cmc import cmc_4h_close_matrix

            close_df = cmc_4h_close_matrix(CONTEST_TOKENS)
        except Exception:
            close_df = None

    base: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "ts": _now_iso(),
        "issuer": settings.agent_name,
        "universe": list(CONTEST_TOKENS),
        "query": query,
        "fear_greed": fg,
        # Honest provenance of the report's inputs. Under FREE_DATA (default, since CMC is gone) the
        # data comes from free, keyless public APIs; the label names them so the buyer knows exactly
        # what backs the report (and it matches what the vlayer Web Proof attests to).
        "data_provenance": (
            "binance:4h-klines + alternative.me:fng + dexscreener:dex"
            if settings.free_data
            else "coinmarketcap:4h-close + fear-greed"
        ),
    }

    if close_df is None or getattr(close_df, "empty", True) or close_df.shape[0] < 50:
        base.update(status="degraded", reason="insufficient CMC candle history",
                    regime_score=None, deploy_cap=None, momentum_ranking=[], target_weights={})
        return base

    try:
        strat_name, decision = _decision(close_df, fg)
        weights = {k: round(float(v), 4) for k, v in decision.weights.items() if v and v > 1e-4}
        ranking = [k for k, _ in sorted(weights.items(), key=lambda kv: kv[1], reverse=True)]
        score = None if decision.score is None else round(float(decision.score), 4)
        cap = None if decision.cap is None else round(float(decision.cap), 4)
    except Exception as e:  # never sink the deliverable on a strategy error
        base.update(status="degraded", reason=f"decision error: {type(e).__name__}",
                    regime_score=None, deploy_cap=None, momentum_ranking=[], target_weights={})
        return base

    rationale = ""
    try:
        from ictbot.agent.rationale import explain

        rationale = explain(fear_greed=fg, regime_score=score or 0.0, deploy_cap=cap or 0.0,
                            weights=weights)
    except Exception:
        rationale = ""

    # Data provenance — the report carries an explicit, buyer-verifiable record of which market
    # surfaces produced it: free feeds (Binance klines + alternative.me Fear&Greed),
    # the Data-MCP composed market-overview SKILL (its tools_used + risk_budget), and the
    # MCP-authoritative basket TA health. All best-effort; absence degrades the field, never the report.
    # SECURITY: only public market data goes in here — never an API key, wallet password, or paths.
    cmc_sources: dict[str, Any] = (
        {"free_apis": ["binance:klines/4h+1d", "alternative.me:fng", "dexscreener:dex"]}
        if settings.free_data
        else {"pro_api": ["cryptocurrency/quotes (4h close)", "fear-and-greed"]}
    )
    overview = None
    ta_health = None
    try:
        from ictbot.data import cmc_agent_hub

        if cmc_agent_hub.enabled():
            ov = cmc_agent_hub.market_overview()
            if ov:
                overview = {
                    "skill_source": ov.get("skill_source"),
                    "risk_budget": ov.get("risk_budget"),
                    "tools_used": ov.get("tools_used"),
                    "notes": ov.get("notes"),
                    # Live, more-dynamic CMC signals so the deliverable reads as genuinely live
                    # (the regime/ranking are daily-stable by design). Public market data only.
                    "regime": ov.get("regime"),
                    "btc_dominance": ov.get("btc_dominance"),
                    "mktcap_change_24h": ov.get("mktcap_change_24h"),
                    "headline": ov.get("headline"),
                    "narratives": ov.get("narratives"),
                }
                cmc_sources["mcp_skill"] = {
                    "name": "market_overview",
                    "tools_used": ov.get("tools_used"),
                    "risk_budget": ov.get("risk_budget"),
                }
            th = cmc_agent_hub.basket_ta_health()  # CMC MCP-authoritative RSI/MACD/EMA health
            if th is not None:
                ta_health = round(float(th), 4)
                cmc_sources["mcp_ta"] = "get_crypto_technical_analysis"
    except Exception:
        overview, ta_health = None, None

    # FREE_DATA: CMC's MCP composed market-overview is gone (the block above no-ops when disabled), so
    # rebuild the SAME-shaped overview + TA health from free APIs (alternative.me + CoinGecko +
    # DexScreener + the Binance close matrix). Best-effort — absence just leaves the fields None.
    if settings.free_data and overview is None:
        try:
            from ictbot.data.free_overview import free_market_overview

            fov = free_market_overview()
            if fov:
                overview = {
                    "skill_source": fov.get("skill_source"),
                    "risk_budget": fov.get("risk_budget"),
                    "tools_used": fov.get("tools_used"),
                    "notes": fov.get("notes"),
                    "regime": fov.get("regime"),
                    "btc_dominance": fov.get("btc_dominance"),
                    "mktcap_change_24h": fov.get("mktcap_change_24h"),
                    "headline": fov.get("headline"),
                    "narratives": fov.get("narratives"),
                }
                cmc_sources["free_overview"] = fov.get("tools_used")
                th = (fov.get("ta_breadth") or {}).get("ta_health")
                if th is not None and ta_health is None:
                    ta_health = round(float(th), 4)
                    cmc_sources["free_ta"] = "binance:close-matrix breadth"
        except Exception:
            pass

    # The agent's live CMC tilt (sector rotation + CMC-native momentum + on-chain signals) from the
    # latest journaled tick — ships the FULL Agent-Hub view, not just the regime score + weights.
    cmc_signals = _cmc_signals()
    if cmc_signals:
        cmc_sources["live_signals"] = sorted(cmc_signals)  # which live-tilt signals are included
        if "sector_rotation" in cmc_signals or "trending_narratives" in cmc_signals:
            cmc_sources.setdefault("mcp_skill", {})
            if isinstance(cmc_sources["mcp_skill"], dict):
                cmc_sources["mcp_skill"]["narratives"] = "trending_crypto_narratives"
        if "onchain" in cmc_signals:
            cmc_sources["ws_onchain"] = "token_metric · holders_metrics · liquidity_event · transaction"

    # FREE_DATA: live DexScreener DEX-native signals (USD price, pooled liquidity, 24h DEX volume,
    # 24h price change, tx count) for the universe — the on-chain data the report ships now that CMC's
    # DEX feeds are gone. Free, keyless, best-effort (absence just omits the field).
    dex_signals = None
    if settings.free_data:
        try:
            from ictbot.data import dexscreener

            ds = dexscreener.dex_signals(list(CONTEST_TOKENS), with_windows=True)
            if ds:
                dex_signals = ds
                cmc_sources["dexscreener"] = "tokens/v1/bsc · price·liquidity·volume·change"
        except Exception:
            dex_signals = None

    base.update(
        status="ok",
        strategy=strat_name,
        regime_score=score,
        deploy_cap=cap,
        ta_health=ta_health,
        momentum_ranking=ranking,
        target_weights=weights,
        rationale=rationale,
        market_overview=overview,
        cmc_signals=cmc_signals,
        dex_signals=dex_signals,
        cmc_sources=cmc_sources,
    )

    # vlayer provenance (flag-gated, best-effort): upgrade `data_provenance` from a CLAIM to a
    # buyer-VERIFIABLE on-chain attestation that the data inputs really came from their source (alternative.me)
    # (zkTLS Web Proof). Read-only + key-free, so it is safe on the zero-secret deploy; absence
    # simply omits the field (never sinks the report). See docs/vlayer/INTEGRATION_PLAN.md.
    try:
        from ictbot.agent import provenance

        proof = provenance.latest_attestation()
        if proof:
            base["provenance_proof"] = proof
            cmc_sources["vlayer_web_proof"] = {
                "chain": proof.get("chain"),
                "verifier": proof.get("verifier"),
                "proven_at": proof.get("proven_at"),
            }
    except Exception:
        pass

    return base
