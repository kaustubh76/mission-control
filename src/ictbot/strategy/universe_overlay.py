"""
CMC universe overlay — tilt allocation WITHIN the validated tradeable set.

Execution stays SAFE: the tradeable universe is still exactly the 8 TWAK-supported
CONTEST_TOKENS (no new BSC addresses). This only re-weights the split AMONG the tokens
the allocator already chose to hold, using CMC's live 7-day relative strength, then
re-normalizes to the SAME total deployment — so the regime-chosen deploy cap and the
cash level are unchanged; only the relative sizing among held tokens tilts.

Gated by `settings.alloc_universe_tilt` (default OFF) at the call site, so with the
flag off the allocator's output is identical to the validated model.
"""

from __future__ import annotations

from statistics import fmean

# Bounded multiplier band — a tilt, never a regime/cap change.
_LO, _HI = 0.85, 1.15


def momentum_tilt(
    weights: dict[str, float], token_changes: dict, *, lo: float = _LO, hi: float = _HI
) -> dict[str, float]:
    """Re-weight `weights` ({sym: w>0}) by each token's CMC 7-day relative strength,
    clamped to [lo, hi] and re-normalized to the SAME total deployment.

    `token_changes` = {sym: {"pct_7d": float, ...}} from cmc_intel.token_changes.
    Tokens with no CMC data keep multiplier 1.0. Returns a NEW dict; never raises.

    The raw multiplier spans [2-hi, hi]; the explicit `max(lo, min(hi, ·))` clamp makes
    `lo` actually bind (CMC-4). At the default symmetric band (lo == 2-hi) the clamp is a
    no-op, so the validated tilt path is byte-identical."""
    if not weights or not token_changes:
        return dict(weights)
    pcts = [token_changes.get(s, {}).get("pct_7d") for s in weights]
    pcts = [p for p in pcts if p is not None]
    if len(pcts) < 2:
        return dict(weights)
    mean = fmean(pcts)
    spread = (max(pcts) - min(pcts)) or 1.0
    tilted: dict[str, float] = {}
    for s, w in weights.items():
        p = token_changes.get(s, {}).get("pct_7d")
        if p is None:
            tilted[s] = w
            continue
        rel = max(-1.0, min(1.0, (p - mean) / spread))  # relative strength ∈ [-1, 1]
        mult = 1.0 + rel * (hi - 1.0)  # raw band [2-hi, hi]
        mult = max(lo, min(hi, mult))  # honor [lo, hi] (asymmetric-safe)
        tilted[s] = w * mult
    total_before, total_after = sum(weights.values()), sum(tilted.values())
    if total_after <= 0:
        return dict(weights)
    scale = total_before / total_after  # preserve total deployment
    return {s: v * scale for s, v in tilted.items()}


# --------------------------------------------------------------------------- #
# CMC live-signal overlays (flow / liquidity / concentration) + cap brake.
# Each re-weights WITHIN the held set (same total deployment) or lowers the cap — live-only,
# clamped, gated. Reads `signals` from strategy.market_signals.token_signals (per-token dict).
# Every one is a no-op at its default param, so the validated path stays byte-identical.
# --------------------------------------------------------------------------- #
def _renorm(weights: dict, adj: dict) -> dict:
    """Rescale `adj` back to the total deployment of `weights` (preserve cap). Falls back to the
    original weights if the adjusted total collapses."""
    tot, atot = sum(weights.values()), sum(adj.values())
    return {s: v * tot / atot for s, v in adj.items()} if atot > 0 else dict(weights)


def liquidity_floor(weights: dict[str, float], signals: dict, min_vol_usd: float) -> dict[str, float]:
    """Drop held tokens whose 24h volume is below `min_vol_usd` and re-concentrate into the liquid
    ones (SAME total deployment). Tokens with unknown volume are KEPT (don't over-filter on missing
    data). No-op at `min_vol_usd<=0`, or if it would empty the book."""
    if min_vol_usd <= 0 or not weights:
        return dict(weights)
    kept = {s: w for s, w in weights.items()
            if (v := signals.get(s, {}).get("volume_24h")) is None or v >= min_vol_usd}
    return _renorm(weights, kept) if kept else dict(weights)


def flow_tilt(weights: dict[str, float], signals: dict, *, w: float = 0.0,
              lo: float = 0.85, hi: float = 1.15) -> dict[str, float]:
    """Re-weight by on-chain buy/sell `flow_ratio` (∈[0,1], 0.5 neutral): net-buying tokens up,
    net-selling down, multiplier clamped to `[lo, hi]`, SAME total deployment. `w` scales the band
    (0 = no-op). Requires ≥2 held tokens with a flow reading — otherwise returns the weights
    UNCHANGED (no partial tilt on sparse data), even if `w` is large."""
    if w <= 0 or not weights:
        return dict(weights)
    flows = {s: signals.get(s, {}).get("flow_ratio") for s in weights}
    if sum(f is not None for f in flows.values()) < 2:
        return dict(weights)
    adj = {}
    for s, wt in weights.items():
        f = flows[s]
        if f is None:
            adj[s] = wt
        else:
            adj[s] = wt * max(lo, min(hi, 1.0 + w * 2.0 * (f - 0.5)))
    return _renorm(weights, adj)


def concentration_penalty(weights: dict[str, float], signals: dict, max_top10_pct: float) -> dict[str, float]:
    """Halve the weight of held tokens whose top-10 holder share exceeds `max_top10_pct` (whale-dump
    risk) and re-concentrate into the rest (SAME total). No-op at `max_top10_pct<=0`."""
    if max_top10_pct <= 0 or not weights:
        return dict(weights)
    adj = {s: (wt * 0.5 if (t := signals.get(s, {}).get("top10_pct")) is not None and t > max_top10_pct else wt)
           for s, wt in weights.items()}
    return _renorm(weights, adj)


def liquidity_cap_brake(weights: dict[str, float], signals: dict, *, liq_brake: float = 0.0,
                        ref_usd: float = 100_000.0) -> float:
    """Deploy MULTIPLIER ∈ (0,1] — haircut the cap when held tokens show adverse net on-chain flow
    (DEX liquidity leaving and/or whales net-selling, deployment-weighted). Only ever lowers (risk-
    reducing). `liq_brake` (0..1) = max haircut, reached when net outflow ≥ `ref_usd`. 1.0 at default."""
    if liq_brake <= 0 or not weights:
        return 1.0
    tot = sum(weights.values()) or 1.0
    net = 0.0
    for s, wt in weights.items():
        sig = signals.get(s, {})
        for k in ("net_liquidity_usd", "whale_net_usd"):
            v = sig.get(k)
            if isinstance(v, (int, float)):
                net += (wt / tot) * v
    if net >= 0:
        return 1.0
    return 1.0 - liq_brake * min(1.0, -net / ref_usd)


def cmc_momentum_tilt(weights: dict[str, float], signals: dict, *, w: float = 0.0,
                      lo: float = _LO, hi: float = _HI) -> dict[str, float]:
    """Re-weight by CMC-NATIVE multi-window momentum — each token's `mom_blend` of pct_24h/7d/30d
    (CMC's own % change, CEX snapshot), cross-sectional relative strength clamped to `[lo, hi]`, SAME
    total deployment. `w` scales the band (0 = no-op). This is the richer, multi-window sibling of
    `momentum_tilt` (which uses pct_7d only); they are INDEPENDENT A/B levers and may both be on.

    A SIZING tilt within the already-held set (not a selection change), so it's strategy-agnostic and
    byte-identical to the validated model at `w=0`. Needs ≥2 held tokens with a CMC momentum reading —
    otherwise returns the weights UNCHANGED (no partial tilt on sparse data). Never raises."""
    from ictbot.strategy.market_signals import mom_blend

    if w <= 0 or not weights:
        return dict(weights)
    moms = {s: mom_blend(signals.get(s, {}) or {}) for s in weights}
    present = [m for m in moms.values() if m is not None]
    if len(present) < 2:
        return dict(weights)
    mean = fmean(present)
    spread = (max(present) - min(present)) or 1.0
    adj: dict[str, float] = {}
    for s, wt in weights.items():
        m = moms[s]
        if m is None:
            adj[s] = wt
            continue
        rel = max(-1.0, min(1.0, (m - mean) / spread))  # relative strength ∈ [-1, 1]
        adj[s] = wt * max(lo, min(hi, 1.0 + w * rel * (hi - 1.0)))
    return _renorm(weights, adj)


def sector_tilt(weights: dict[str, float], trending, token_sectors: dict, *, w: float = 0.0,
                lo: float = _LO, hi: float = _HI) -> dict[str, float]:
    """Rotate the held book toward CMC's trending narratives — boost held tokens whose CMC categories
    intersect `trending` (the live `trending_crypto_narratives` list), clamped to `[lo, hi]`, then
    re-normalize so non-trending names naturally fade at the SAME total deployment.

    `token_sectors` = {SYM: set(CMC category names)}. A held token in ≥1 trending category gets
    `min(hi, 1 + w)`; others stay 1.0. No-op when `w<=0`, `trending` empty, or NO held token matches
    (and — via the renorm of equal multipliers — when ALL held tokens match: no differential, no tilt).
    Never raises."""
    if w <= 0 or not weights or not trending:
        return dict(weights)
    trend = {str(t).strip().lower() for t in trending if t}
    if not trend:
        return dict(weights)
    boost = min(hi, 1.0 + w)
    adj: dict[str, float] = {}
    hits = 0
    for s, wt in weights.items():
        secs = token_sectors.get(s) or ()
        if any(str(sec).strip().lower() in trend for sec in secs):
            adj[s] = wt * boost
            hits += 1
        else:
            adj[s] = wt
    if hits == 0:
        return dict(weights)
    return _renorm(weights, adj)
