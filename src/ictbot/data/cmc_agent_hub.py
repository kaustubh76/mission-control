"""
CMC Agent Hub client — the Data **MCP** (12 pre-computed tools) + a composed market-overview
**skill** built on top of them.

This is the "Best Use of CoinMarketCap" layer. The agent talks to CMC's hosted MCP server
(`https://mcp.coinmarketcap.com/mcp`, authenticated with the SAME Startup key via the
`X-CMC-MCP-API-KEY` header — verified) to read CMC's AUTHORITATIVE pre-computed signals
instead of computing them in-context. The live `tools/list` exposes these 12 Data-MCP tools
(confirmed by `scripts/probe_agent_hub.py`):
  get_crypto_quotes_latest, get_crypto_info, search_cryptos, search_crypto_info,
  get_crypto_technical_analysis, get_crypto_marketcap_technical_analysis, get_crypto_metrics,
  get_global_metrics_latest, get_global_crypto_derivatives_metrics, trending_crypto_narratives,
  get_upcoming_macro_events, get_crypto_latest_news.

HONEST LABELING: CMC's **Skills Marketplace** (https://coinmarketcap.com/api/skills-marketplace/)
is a SEPARATE product — an agent-side router over skill pipelines. It is NOT exposed as callable
JSON-RPC tools on the MCP endpoint (the `/skills*` paths 404; only the 12 Data-MCP tools are
callable — see the probe). So `market_overview()` is OUR OWN **composed** skill (it stitches
several Data-MCP tools into an agent-ready regime read with a numeric **risk budget** ∈ [0,1]
that modulates the deploy cap). Its `skill_source` is therefore "composed"; if CMC later exposes
a callable marketplace skill, wire it and set "cmc-marketplace". LIVE-only / forward-validated —
the narrative + macro output is not historically backtestable.

EVERYTHING here is read-only data, flag-gated (`CMC_MCP_ENABLED`, default OFF), TTL-cached,
and NEVER raises into a tick (degrades to None). Usage is journaled for the dashboard.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ictbot.settings import JOURNAL_DIR, settings

# `market_overview()` is our composition of CMC Data-MCP tools, not a call into CMC's hosted
# Skills Marketplace (no callable skill endpoint exists — see the module docstring + probe).
SKILL_SOURCE = "composed"

# The full Data-MCP catalog exposed by CMC's hosted MCP server's `tools/list` — verified live by
# `scripts/probe_agent_hub.py` (data/journal/cmc_agent_hub_probe.json). `market_overview()` exercises
# a subset (8); the dashboard shows exercised/available so the full surface is visible. One source of
# truth — keep in sync with the module docstring.
MCP_TOOLS: tuple[str, ...] = (
    "get_crypto_quotes_latest",
    "get_crypto_info",
    "search_cryptos",
    "search_crypto_info",
    "get_crypto_technical_analysis",
    "get_crypto_marketcap_technical_analysis",
    "get_crypto_metrics",
    "get_global_metrics_latest",
    "get_global_crypto_derivatives_metrics",
    "trending_crypto_narratives",
    "get_upcoming_macro_events",
    "get_crypto_latest_news",
)

# Contest universe → CMC numeric ids (verified live via the MCP get_crypto_quotes_latest).
CMC_IDS: dict[str, int] = {
    "BNB": 1839,
    "ETH": 1027,
    "CAKE": 7186,
    "LINK": 1975,
    "UNI": 7083,
    "AVAX": 5805,
    "DOT": 6636,
    "DOGE": 74,
}

_USAGE_PATH = JOURNAL_DIR / "cmc_mcp_usage.json"
_TTL_S = 3600.0  # daily signals → 1h cache is plenty
_cache: dict[str, tuple[float, object]] = {}
_usage = {"calls": 0, "by_tool": {}, "last_call_ts": None, "last_error": None}


def enabled() -> bool:
    """True iff the MCP layer is switched on and a key is present."""
    return bool(settings.cmc_mcp_enabled and settings.cmc_api_key)


def _record(tool: str, ok: bool, err: str | None = None) -> None:
    _usage["calls"] += 1
    _usage["by_tool"][tool] = _usage["by_tool"].get(tool, 0) + 1
    _usage["last_call_ts"] = int(time.time())
    if err:
        _usage["last_error"] = err[:200]
    try:
        tmp = _USAGE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_usage))
        tmp.replace(_USAGE_PATH)
    except Exception:
        pass


def telemetry() -> dict:
    """Dashboard view: MCP call counts + last activity (read from disk if not in-proc)."""
    if _usage["calls"] == 0 and _USAGE_PATH.exists():
        try:
            return {"enabled": enabled(), **json.loads(_USAGE_PATH.read_text())}
        except Exception:
            pass
    return {"enabled": enabled(), **_usage}


def _rpc(method: str, params: dict, timeout: float = 30.0) -> dict | None:
    """One JSON-RPC call to the CMC MCP server. Never raises — returns None on any failure."""
    if not enabled():
        return None
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        settings.cmc_mcp_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-CMC-MCP-API-KEY": settings.cmc_api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        _usage["last_error"] = f"http {e.code}"
        return None
    except Exception as e:  # noqa: BLE001 — best-effort data read
        _usage["last_error"] = type(e).__name__
        return None


def call_tool(name: str, arguments: dict | None = None, *, ttl: float = _TTL_S):
    """Call an MCP tool, parse its JSON payload (flat object OR a {headers, rows} table).
    TTL-cached + usage-journaled. Returns the parsed object/list, or None on failure."""
    key = f"{name}:{json.dumps(arguments or {}, sort_keys=True)}"
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    res = _rpc("tools/call", {"name": name, "arguments": arguments or {}})
    content = ((res or {}).get("result") or {}).get("content") or []
    if not content:
        _record(name, ok=False, err=(res or {}).get("error") and "rpc_error")
        return None
    try:
        payload = json.loads(content[0].get("text", "null"))
    except Exception:
        payload = content[0].get("text")
    out = (
        _table_to_dicts(payload) if isinstance(payload, dict) and "headers" in payload else payload
    )
    _cache[key] = (time.time(), out)
    _record(name, ok=out is not None)
    return out


def _table_to_dicts(tbl: dict) -> list[dict]:
    """CMC MCP returns table tools as {headers:[...], rows:[[...]]} → list of dicts."""
    headers, rows = tbl.get("headers") or [], tbl.get("rows") or []
    return [dict(zip(headers, row, strict=False)) for row in rows]


def live_tools() -> list[str]:
    """Live `tools/list` from the CMC MCP server → the callable tool names. [] when the MCP layer
    is disabled or on any failure (never raises) — the read-only health surface for `make mcp_check`."""
    listing = _rpc("tools/list", {})
    defs = ((listing or {}).get("result") or {}).get("tools") or []
    return [t.get("name") for t in defs if isinstance(t, dict) and t.get("name")]


def ping() -> dict:
    """Live health probe of the CMC MCP: is it enabled, how many tools respond, and does a sample
    `tools/call` return data. Read-only (one tools/list + one cheap tools/call); never raises.
    Returns {enabled, tools_live, tools, sample_ok, last_error}."""
    if not enabled():
        return {
            "enabled": False,
            "tools_live": 0,
            "tools": [],
            "sample_ok": False,
            "last_error": "disabled (CMC_MCP_ENABLED off or no CMC_API_KEY)",
        }
    tools = live_tools()
    sample_ok = global_metrics() is not None  # one real tools/call as a liveness check
    return {
        "enabled": True,
        "tools_live": len(tools),
        "tools": tools,
        "sample_ok": sample_ok,
        "last_error": _usage.get("last_error"),
    }


# ---- typed tool wrappers ---------------------------------------------------- #
def _to_float(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def technical_analysis(symbol_or_id: str | int) -> dict | None:
    """CMC's pre-computed DAILY TA for one asset → {rsi:{rsi7/14/21}, macd:{macdLine,
    signalLine,histogram}, moving_averages:{sma/ema 7/30/200}, fibonacciLevels, pivotPoint}."""
    cid = symbol_or_id if str(symbol_or_id).isdigit() else CMC_IDS.get(str(symbol_or_id).upper())
    if cid is None:
        return None
    out = call_tool("get_crypto_technical_analysis", {"id": str(cid)})
    return out if isinstance(out, dict) else None


def technicals_for(symbols=tuple(CMC_IDS)) -> dict[str, dict]:
    """Per-token CMC TA across the contest universe (skips any that fail). Cached per token."""
    out = {}
    for s in symbols:
        ta = technical_analysis(s)
        if ta:
            out[s] = ta
    return out


def global_metrics() -> dict | None:
    out = call_tool("get_global_metrics_latest", {})
    if isinstance(out, list) and out:
        return out[0]
    return out if isinstance(out, dict) else None


def derivatives_metrics() -> dict | None:
    out = call_tool("get_global_crypto_derivatives_metrics", {})
    if isinstance(out, list) and out:
        return out[0]
    return out if isinstance(out, dict) else None


def trending_narratives(limit: int = 5) -> list[str]:
    """Top trending narrative/category names (e.g. 'Binance Ecosystem', 'Layer 1')."""
    out = call_tool("trending_crypto_narratives", {})
    cl = out.get("categoryList", {}) if isinstance(out, dict) else {}
    rows, hdr = cl.get("rows") or [], cl.get("headers") or []
    if "categoryName" in hdr:
        ni = hdr.index("categoryName")
        return [r[ni] for r in rows[:limit] if ni < len(r)]
    return []


# CMC's global-metrics MCP tool returns nested, LLM-formatted strings — these pull the
# numeric values our risk budget needs out of that structure.
def _pct(s) -> float | None:
    """'+0.71179%' / '-3.9%' → float percent. None if unparseable."""
    try:
        return float(str(s).replace("%", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


_UNIT_MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _unit_num(s) -> float | None:
    """CMC's derivatives/mktcap tools return human strings like '360.64 B' / '2.18 T' /
    '203.04 T'. Parse to a float USD value. Plain numbers pass through. None if unparseable."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = str(s).replace(",", "").replace("$", "").strip()
    if not txt:
        return None
    mult = 1.0
    if txt[-1].upper() in _UNIT_MULT:
        mult = _UNIT_MULT[txt[-1].upper()]
        txt = txt[:-1].strip()
    try:
        return float(txt) * mult
    except ValueError:
        return None


def gm_fear_greed(gm: dict | None) -> int | None:
    try:
        return int(gm["sentiment"]["fear_greed"]["current"]["index"])
    except (KeyError, TypeError, ValueError):
        return None


def gm_btc_dominance(gm: dict | None) -> float | None:
    try:
        return _pct(gm["dominance"]["btc"]["current"])
    except (KeyError, TypeError):
        return None


def gm_mktcap_change_24h(gm: dict | None) -> float | None:
    try:
        return _pct(gm["market_size"]["total_crypto_market_cap_usd"]["percent_change"]["24h"])
    except (KeyError, TypeError):
        return None


# ---- the Skill: a composed market-overview pipeline ------------------------- #
def _ta_risk_budget(symbols=tuple(CMC_IDS)) -> tuple[float | None, dict]:
    """Risk budget from CMC's pre-computed per-token TA: breadth of bullish-MACD +
    above-EMA names, braked by overbought RSI. In [0,1]; None if no TA available."""
    tas = technicals_for(symbols)
    if not tas:
        return None, {}
    bull = above = ob = n = 0
    for ta in tas.values():
        hist = _to_float((ta.get("macd") or {}).get("histogram"))
        rsi14 = _to_float((ta.get("rsi") or {}).get("rsi14"))
        ema30 = _to_float(
            (ta.get("moving_averages") or {}).get("exponential_moving_average_30_day")
        )
        # 'pivotPoint' ~ last price proxy; compare to EMA30 for above/below trend.
        px = _to_float(ta.get("pivotPoint"))
        if hist is None or rsi14 is None:
            continue
        n += 1
        bull += int(hist > 0)
        if px is not None and ema30 is not None:
            above += int(px > ema30)
        ob += int(rsi14 > 70)
    if n == 0:
        return None, {}
    health = 0.5 * (bull / n) + 0.5 * (above / n) - 0.5 * max(0.0, ob / n - 0.30)
    return max(0.0, min(1.0, health)), {
        "tokens": n,
        "macd_bull": bull,
        "above_ema": above,
        "overbought": ob,
    }


def token_ta_scores(symbols=tuple(CMC_IDS)) -> dict[str, float]:
    """Per-token CMC TA CONFIRMATION in [0,1] for the LIVE ranking — the same formula
    `strategy.technicals.token_ta_score` uses for the backtest (0.40 MACD-bull + 0.35
    above-EMA + 0.25 RSI-health − 0.20 overbought), but read from CMC's authoritative
    pre-computed per-token TA. Tokens whose TA can't be read are omitted (caller treats
    a missing score as neutral)."""
    out: dict[str, float] = {}
    for sym, ta in technicals_for(symbols).items():
        hist = _to_float((ta.get("macd") or {}).get("histogram"))
        rsi14 = _to_float((ta.get("rsi") or {}).get("rsi14"))
        ema30 = _to_float(
            (ta.get("moving_averages") or {}).get("exponential_moving_average_30_day")
        )
        px = _to_float(ta.get("pivotPoint"))
        if hist is None or rsi14 is None:
            continue
        macd_pos = 1.0 if hist > 0 else 0.0
        above = 1.0 if (px is not None and ema30 is not None and px > ema30) else 0.0
        rsi_health = max(0.0, min(1.0, 1.0 - abs(rsi14 - 60.0) / 40.0))
        ob_pen = 1.0 if rsi14 > 70 else 0.0
        out[sym] = max(
            0.0, min(1.0, 0.40 * macd_pos + 0.35 * above + 0.25 * rsi_health - 0.20 * ob_pen)
        )
    return out


def basket_ta_health() -> float | None:
    """CMC-AUTHORITATIVE basket TA trend-health in [0,1] for the LIVE deploy cap — the
    same signal `strategy.technicals.trend_health` computes locally for the backtest, but
    read from CMC's pre-computed per-token TA. None if the MCP read fails (caller falls
    back to the local compute)."""
    return _ta_risk_budget()[0] if enabled() else None


# ---- more Data-MCP tools, wired into real decisions (each gated, never-raise) -------- #
def quotes_latest(symbols=tuple(CMC_IDS)) -> dict[str, dict]:
    """CMC's authoritative latest quote per contest token → {sym: {price, pct_24h, pct_7d,
    volume_24h, market_cap, symbol_cmc}}. One batched `get_crypto_quotes_latest` call. The
    cap layer cross-checks these against the candle feed; the panel shows live price/vol."""
    ids = [CMC_IDS[s] for s in symbols if s in CMC_IDS]
    if not ids:
        return {}
    out = call_tool("get_crypto_quotes_latest", {"id": ",".join(str(i) for i in ids)})
    by_id = {str(r.get("id")): r for r in (out or []) if isinstance(r, dict)}
    res: dict[str, dict] = {}
    for s in symbols:
        r = by_id.get(str(CMC_IDS.get(s)))
        if r:
            res[s] = {
                "price": _to_float(r.get("price")),
                "pct_24h": _to_float(r.get("percent_change_24h")),
                "pct_7d": _to_float(r.get("percent_change_7d")),
                "volume_24h": _to_float(r.get("volume_24h")),
                "market_cap": _to_float(r.get("market_cap")),
                "symbol_cmc": r.get("symbol"),
            }
    return res


def verify_cmc_ids() -> dict:
    """Proof that our hardcoded CMC_IDS map resolves to the right assets — one
    `get_crypto_quotes_latest` over the whole universe, comparing CMC's returned symbol to
    ours. {matched, total, mismatches}. Used by the probe/tests + shown on the dashboard."""
    out = call_tool(
        "get_crypto_quotes_latest", {"id": ",".join(str(i) for i in CMC_IDS.values())}, ttl=0
    )
    by_id = {
        str(r.get("id")): str(r.get("symbol") or "").upper()
        for r in (out or [])
        if isinstance(r, dict)
    }
    matched, mismatches = 0, {}
    for sym, cid in CMC_IDS.items():
        got = by_id.get(str(cid))
        if got == sym:
            matched += 1
        else:
            mismatches[sym] = {"expected_id": cid, "cmc_symbol": got}
    return {"matched": matched, "total": len(CMC_IDS), "mismatches": mismatches}


def derivatives_stress() -> tuple[float | None, dict]:
    """Leverage/funding fragility in [0,1] from `get_global_crypto_derivatives_metrics`
    (higher = more fragile → brake the deploy cap). Rises when open interest is BUILDING
    (24h OI up) and funding is stretched. None if the read fails."""
    d = derivatives_metrics()
    if not isinstance(d, dict):
        return None, {}
    oi = d.get("totalOpenInterest") or {}
    fr = d.get("fundingRate") or {}
    oi24 = _pct(oi.get("percentage_change_24h"))
    funding = _to_float(fr.get("current"))
    if oi24 is None and funding is None:
        return None, {}
    oi_term = max(0.0, min(1.0, (oi24 or 0.0) / 25.0))  # +25%/24h OI surge → full
    fund_term = max(0.0, min(1.0, abs(funding or 0.0) / 0.05))  # |funding| 5% → full
    stress = max(0.0, min(1.0, 0.7 * oi_term + 0.3 * fund_term))
    return stress, {
        "stress": round(stress, 4),
        "oi_change_24h": oi24,
        "funding_rate": funding,
        "open_interest_usd": _unit_num(oi.get("current")),
    }


# High-impact macro catalysts the agent should de-risk INTO (substring match on the title).
_MACRO_HIGH = (
    "fomc",
    "federal reserve",
    "interest rate",
    "rate decision",
    "rate cut",
    "cpi",
    "inflation",
    "powell",
    "pce",
    "nonfarm",
    "jobs report",
    "fed ",
)


def _parse_event_date(s) -> datetime | None:
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def macro_events(limit: int = 6) -> list[dict]:
    """Upcoming market-moving macro events from `get_upcoming_macro_events`
    → [{title, event_date, url, when(datetime|None), high_impact}]."""
    out = call_tool("get_upcoming_macro_events", {})
    tbl = out.get("upcomingEventNews") if isinstance(out, dict) else None
    hdr = (tbl or {}).get("headers") or []
    rows = (tbl or {}).get("rows") or []
    events = []
    for r in rows:
        rec = dict(zip(hdr, r, strict=False))
        title = str(rec.get("title") or "")
        events.append(
            {
                "title": title,
                "event_date": rec.get("eventDate"),
                "url": rec.get("url"),
                "when": _parse_event_date(rec.get("eventDate")),
                "high_impact": any(k in title.lower() for k in _MACRO_HIGH),
            }
        )
    return events[:limit]


def next_macro_event() -> dict | None:
    """The nearest UPCOMING macro event (for the de-risk guard + dashboard).
    {title, event_date, url, hours_to, high_impact} or None."""
    now = datetime.now(timezone.utc)
    best = None
    for e in macro_events(8):
        w = e.get("when")
        if w is None:
            continue
        hours = (w - now).total_seconds() / 3600.0
        if hours < -12:  # already passed
            continue
        if best is None or hours < best["hours_to"]:
            best = {
                "title": e["title"],
                "event_date": e["event_date"],
                "url": e.get("url"),
                "hours_to": round(hours, 1),
                "high_impact": e["high_impact"],
            }
    return best


# Headline-risk keywords for the optional news brake (conservative, substring match).
_NEWS_NEG = (
    "hack",
    "exploit",
    "lawsuit",
    "sec sues",
    "ban",
    "crash",
    "plunge",
    "liquidat",
    "collapse",
    "fraud",
    "sell-off",
    "selloff",
    "depeg",
)


def latest_news(symbol_or_id="1", limit: int = 5) -> list[dict]:
    """Latest news for an asset (default BTC id=1, a market proxy) from
    `get_crypto_latest_news` → [{title, url, published_at}]. `id` is REQUIRED by the tool."""
    cid = symbol_or_id if str(symbol_or_id).isdigit() else CMC_IDS.get(str(symbol_or_id).upper(), 1)
    out = call_tool("get_crypto_latest_news", {"id": str(cid), "limit": limit})
    items = (
        out if isinstance(out, list) else ((out or {}).get("news") if isinstance(out, dict) else [])
    )
    res = []
    for it in (items or [])[:limit]:
        if isinstance(it, dict):
            res.append(
                {
                    "title": it.get("title"),
                    "url": it.get("url"),
                    "published_at": it.get("publishedAt"),
                }
            )
    return res


def mktcap_technical_analysis() -> dict | None:
    """CMC's pre-computed TA on the TOTAL crypto market cap (a global-regime read) from
    `get_crypto_marketcap_technical_analysis`. Values are human unit-strings; we surface
    RSI14, MACD-histogram sign, total mktcap, and a [0,1] health. None on failure."""
    out = call_tool("get_crypto_marketcap_technical_analysis", {})
    if not isinstance(out, dict):
        return None
    rsi14 = _to_float((out.get("rsi") or {}).get("rsi14"))
    macd_hist = _unit_num((out.get("macd") or {}).get("histogram"))
    if rsi14 is None and macd_hist is None:
        return None
    health = None
    if rsi14 is not None:
        health = max(0.0, min(1.0, rsi14 / 100.0))
        if macd_hist is not None:
            health = max(0.0, min(1.0, health + (0.05 if macd_hist > 0 else -0.05)))
    return {
        "rsi14": rsi14,
        "macd_histogram": macd_hist,
        "market_cap_usd": _unit_num(out.get("currentMarketCap")),
        "health": None if health is None else round(health, 4),
    }


# ---- the Skill: a composed market-overview pipeline ------------------------- #
def market_overview() -> dict | None:
    """Our **composed** market-overview skill (skill_source="composed"): stitches CMC
    Data-MCP tools into an agent-ready regime read with a numeric risk budget ∈ [0,1] that
    modulates the deploy cap. NOT a call into CMC's hosted Skills Marketplace (no callable
    skill endpoint exists — see the module docstring).

    Base budget = TA breadth + F&G + market-cap pulse. Optional, individually flag-gated
    CMC reads enrich it: market-cap TA (CMC_MKTCAP_TA), a derivatives leverage brake
    (CMC_DERIV_BRAKE), a macro-event de-risk guard (CMC_MACRO_GUARD), per-token quotes
    (CMC_QUOTES_XCHECK), and news headlines (CMC_NEWS_ENABLED / CMC_NEWS_BRAKE). Each read
    is also returned for the dashboard. Returns None if nothing could be read. LIVE-only,
    forward-validated — the macro/narrative output is not historically backtestable."""
    if not enabled():
        return None
    ta_budget, ta_detail = _ta_risk_budget()
    gm = global_metrics() or {}
    narr = trending_narratives(3)
    tools_used = [
        "get_crypto_technical_analysis",
        "get_global_metrics_latest",
        "trending_crypto_narratives",
    ]
    notes: list[str] = []

    fng = gm_fear_greed(gm)
    btc_dom = gm_btc_dominance(gm)
    mc_chg = gm_mktcap_change_24h(gm)

    # Base risk budget = blend of TA breadth + sentiment + market-cap pulse (each optional).
    parts = [(ta_budget, 1.0)]
    if fng is not None:
        parts.append((max(0.0, min(1.0, fng / 100.0)), 1.0))
    if mc_chg is not None:
        parts.append((1.0 if mc_chg > 0 else 0.0, 0.5))

    # (A) market-cap-level TA → an extra regime term in the budget (CMC_MKTCAP_TA).
    mktcap = None
    if settings.cmc_mktcap_ta:
        mktcap = mktcap_technical_analysis()
        if mktcap and mktcap.get("health") is not None:
            parts.append((mktcap["health"], 0.5))
            tools_used.append("get_crypto_marketcap_technical_analysis")

    parts = [(v, w) for v, w in parts if v is not None]
    if not parts:
        return None
    budget = sum(v * w for v, w in parts) / sum(w for _, w in parts)

    # (B) derivatives leverage/funding stress → multiplicative brake (CMC_DERIV_BRAKE).
    derivatives = None
    if settings.cmc_deriv_brake:
        ds, derivatives = derivatives_stress()
        if derivatives:
            tools_used.append("get_global_crypto_derivatives_metrics")
        if ds is not None:
            budget *= 1.0 - settings.cmc_deriv_brake_w * ds
            if ds > 0.05:
                notes.append(f"deriv stress {ds:.0%}")

    # (C) macro-event guard → cap haircut into a high-impact catalyst (CMC_MACRO_GUARD).
    macro = None
    if settings.cmc_macro_guard:
        macro = next_macro_event()
        if macro:
            tools_used.append("get_upcoming_macro_events")
            if (
                macro.get("high_impact")
                and macro.get("hours_to") is not None
                and 0 <= macro["hours_to"] <= settings.cmc_macro_guard_hours
            ):
                budget *= 1.0 - settings.cmc_macro_guard_haircut
                notes.append(
                    f"macro guard −{settings.cmc_macro_guard_haircut:.0%} ({macro['title'][:28]})"
                )

    # (D) per-token quotes cross-check (display + ID-resolution proof) (CMC_QUOTES_XCHECK).
    quotes = None
    if settings.cmc_quotes_xcheck:
        quotes = quotes_latest() or None
        if quotes:
            tools_used.append("get_crypto_quotes_latest")

    # (E) latest news headlines (display-first; optional brake) (CMC_NEWS_ENABLED/_BRAKE).
    news = None
    if settings.cmc_news_enabled:
        news = latest_news("1", 4) or None
        if news:
            tools_used.append("get_crypto_latest_news")
            if settings.cmc_news_brake:
                neg = sum(
                    1
                    for n in news
                    if any(k in str(n.get("title") or "").lower() for k in _NEWS_NEG)
                )
                if neg:
                    budget *= 1.0 - settings.cmc_news_brake_w * min(1.0, neg / len(news))
                    notes.append(f"news brake ({neg} neg)")

    budget = max(0.0, min(1.0, budget))
    regime = "risk-on" if budget >= 0.6 else ("risk-off" if budget <= 0.4 else "neutral")
    headline = (
        f"CMC composed market-overview: {regime} (risk budget {budget:.0%}). "
        f"TA breadth {ta_detail.get('macd_bull', '–')}/{ta_detail.get('tokens', '–')} bullish-MACD"
        + (f", F&G {fng}" if fng is not None else "")
        + (("; " + "; ".join(notes)) if notes else "")
    )
    return {
        "skill_source": SKILL_SOURCE,
        "risk_budget": round(budget, 4),
        "regime": regime,
        "fear_greed": fng,
        "btc_dominance": btc_dom,
        "mktcap_change_24h": mc_chg,
        "ta_breadth": ta_detail,
        "headline": headline,
        "narratives": narr,
        "tools_used": sorted(set(tools_used)),
        "derivatives": derivatives,
        "mktcap_ta": mktcap,
        "next_macro_event": macro,
        "quotes_cross_check": quotes,
        "top_news": news,
    }
