"""
On-chain (DEX) strategy signals — derived from the CMC `onchain@*` WebSocket harvest
(`cmc_stream_store`), for the real-ERC20 subset (CAKE/LINK/UNI).

These turn the raw on-chain flow/holder/liquidity data into per-token signals an allocator can
read:
  - **flow_ratio**     — buy_vol / (buy_vol + sell_vol) over a window. >0.5 = net on-chain buying
                         (a flow confirmation for momentum); <0.5 = net selling.
  - **unique_traders** — participation breadth (more distinct traders = healthier move).
  - **concentration**  — top-10 / top-100 holder %. High = whale-dump risk (a risk filter).
  - **net_liquidity**  — net add−remove USD over the last hour. Negative = liquidity LEAVING (risk).

Everything is pure-ish (reads the local snapshot store, never the network) and never raises. Like
the existing derivatives/macro brakes, on-chain signals are **live-only / forward-validated** —
there is no on-chain history to A/B on CMC daily, so they are journaled each tick (the
forward-validation record) and any allocation use is flag-gated, default OFF.
"""

from __future__ import annotations

from ictbot.data import cmc_stream_store


def flow_ratio(metric_rec: dict, window: str = "24h") -> float | None:
    """buy_vol_usd / (buy_vol_usd + sell_vol_usd) for `window` of a token_metric record. None if
    the window or buy/sell volumes are missing. ∈ [0,1]; 0.5 = balanced, >0.5 = net buying."""
    w = (metric_rec.get("windows") or {}).get(window) if isinstance(metric_rec, dict) else None
    if not isinstance(w, dict):
        return None
    bv, sv = w.get("buy_vol_usd"), w.get("sell_vol_usd")
    if not isinstance(bv, (int, float)) or not isinstance(sv, (int, float)):
        return None
    tot = bv + sv
    return None if tot <= 0 else max(0.0, min(1.0, bv / tot))


def token_signal(sym: str, *, window: str = "24h", liq_window_s: int = 3600) -> dict | None:
    """Per-token on-chain signal dict, or None if the token has no fresh on-chain data:
    {flow_ratio, unique_traders, vol_usd, top10_pct, top100_pct, net_liquidity_usd}. Each field
    independently optional (a channel may be stale while another is fresh)."""
    metrics = cmc_stream_store.onchain_token_metrics().get(sym) or {}
    holders = cmc_stream_store.onchain_holders().get(sym) or {}
    net_liq = cmc_stream_store.onchain_net_liquidity_usd(sym, window_s=liq_window_s)
    w = (metrics.get("windows") or {}).get(window) or {}
    out = {
        "flow_ratio": flow_ratio(metrics, window),
        "unique_traders": w.get("unique_traders"),
        "vol_usd": w.get("vol_usd"),
        "top10_pct": holders.get("top10_pct"),
        "top100_pct": holders.get("top100_pct"),
        "net_liquidity_usd": net_liq,
    }
    return out if any(v is not None for v in out.values()) else None


def onchain_signals(symbols=None, *, window: str = "24h") -> dict:
    """`{SYM: {flow_ratio, unique_traders, vol_usd, top10_pct, top100_pct, net_liquidity_usd}}`
    for every mapped token with fresh on-chain data — the per-tick journal payload. `{}` if the
    on-chain feed is cold. `symbols` defaults to the mapped on-chain subset."""
    from ictbot.data.cmc_onchain import onchain_tokens

    syms = symbols if symbols is not None else list(onchain_tokens())
    out: dict = {}
    for s in syms:
        sig = token_signal(s, window=window)
        if sig is not None:
            out[s] = sig
    return out
