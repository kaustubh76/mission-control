"""
Per-tick natural-language decision rationale — the agent's "voice".

The design goal: an agent that "reads markets, decides, and acts" — not a silent bot.
Each rebalance, `explain(...)` turns the real signals (Fear&Greed, the regime
score, the chosen deployment cap, and the target weights) into one plain-language
paragraph the agent prints + journals. It is template-based on the actual numbers —
no LLM, no fabrication — so it is always faithful to what the agent did.
"""

from __future__ import annotations


def _fg_word(fg: int | None) -> str:
    if fg is None:
        return "unknown"
    if fg <= 24:
        return "extreme fear"
    if fg <= 44:
        return "fear"
    if fg <= 55:
        return "neutral"
    if fg <= 74:
        return "greed"
    return "extreme greed"


def _regime_word(s: float) -> str:
    if s < 0.20:
        return "risk-off"
    if s < 0.50:
        return "cautious"
    if s < 0.75:
        return "constructive"
    return "risk-on"


def _macro_clause(intel: dict | None) -> str:
    """One clause describing the CMC Startup-tier macro context (empty when absent)."""
    if not intel:
        return ""
    bits = []
    bd, bdp = intel.get("btc_dominance"), intel.get("btc_dominance_prev")
    if bd is not None and bdp:
        bits.append(f"BTC dominance {'falling' if bd < bdp else 'rising'} ({bdp:.0f}%→{bd:.0f}%)")
    mc, mcp = intel.get("total_mktcap"), intel.get("total_mktcap_prev")
    if mc is not None and mcp:
        bits.append(f"total mktcap {'expanding' if mc > mcp else 'contracting'}")
    fn, fa = intel.get("fng_now"), intel.get("fng_7d_avg")
    if fn is not None and fa is not None:
        bits.append(f"sentiment {'improving' if fn > fa else 'cooling'} vs its 7d avg")
    return (" CMC macro: " + "; ".join(bits) + ".") if bits else ""


def explain(
    *,
    fear_greed: int | None = None,
    regime_score: float = 0.0,
    deploy_cap: float = 0.0,
    weights: dict[str, float] | None = None,
    funding: float | None = None,
    intel: dict | None = None,
) -> str:
    """Return a one-paragraph natural-language rationale for the current decision.

    `intel` (the CMC Startup-tier macro snapshot, or None) adds a faithful macro clause
    when the enhanced regime is active; absent it, the wording is unchanged."""
    weights = {k: v for k, v in (weights or {}).items() if v > 1e-4}
    deployed = sum(weights.values())
    cash = max(0.0, 1.0 - deployed)
    fg = (
        f"CMC Fear & Greed is {fear_greed} ({_fg_word(fear_greed)})"
        if fear_greed is not None
        else "CMC sentiment is unavailable"
    )
    head = (
        f"{fg}; my risk-on score is {regime_score:.2f} ({_regime_word(regime_score)}), "
        f"so I'm capping deployment at {deploy_cap:.0%} of book."
    )
    if funding is not None:
        head += f" (perp funding {funding:+.3%}.)"
    head += _macro_clause(intel)
    if not weights:
        return head + " Nothing is in an uptrend, so I'm holding 100% USDT and waiting."
    held = ", ".join(f"{v:.0%} {k}" for k, v in sorted(weights.items(), key=lambda kv: -kv[1]))
    return (
        head + f" I'm deploying {deployed:.0%} of book — {held} "
        f"(inverse-vol weighted) — and keeping {cash:.0%} in USDT."
    )
