"""
Read helpers for the dashboard API — the ccxt-free, network-light data layer.

Every function here either reads a JSON/JSONL artifact the allocator already wrote
or calls a key-free pure helper (`identity.profile`, `strategy_spec.summary`,
`heartbeat.age_seconds`). It NEVER constructs a live broker (no ccxt) and never
blocks the poll loop on the network: current weights/regime/F&G are taken from the
latest journal row, and the only optional network call (live CMC Fear&Greed) is
TTL-cached and lazily imported.

Design notes baked in from the plan's risk review:
  - Journal reads are bounded to a tail (default 500 lines) and tolerate a
    truncated final line (a tick caught mid-write).
  - Paths come from `settings.JOURNAL_DIR` (absolute), so reads are CWD-safe.
  - F&G prefers the value embedded in the latest journal row (zero network).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

from ictbot.settings import DATA_DIR, JOURNAL_DIR, settings

# "sim" = paper forward run (pre-contest); "live" = the real contest track.
_LIVE = settings.dashboard_journal == "live"
JOURNAL = JOURNAL_DIR / ("allocator_live.jsonl" if _LIVE else "allocator_journal.jsonl")
STATE = JOURNAL_DIR / ("allocator_live_state.json" if _LIVE else "allocator_state.json")

# Live-push override: the local tick POSTs a fresh snapshot to /api/ingest/snapshot after each
# rebalance; it lands here, and the /api/snapshot route serves it (when fresh) instead of the
# baked seed. Written by `store_pushed_snapshot`, read by `pushed_snapshot`. See ingest.py.
PUSHED = DATA_DIR / "_pushed_snapshot.json"

DEFAULT_TAIL = 500
_FG_TTL_S = 60.0
_fg_cache: dict = {"value": None, "ts": 0.0}


# --------------------------------------------------------------------------- #
# Low-level artifact reads
# --------------------------------------------------------------------------- #
def read_journal(limit: int = DEFAULT_TAIL) -> list[dict]:
    """Last `limit` parsed JSONL rows, oldest-first. Skips blank/corrupt lines
    (e.g. a final line caught mid-write)."""
    if not JOURNAL.exists():
        return []
    try:
        lines = JOURNAL.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except (ValueError, TypeError):
            continue
    return out


def read_state() -> dict:
    if not STATE.exists():
        return {"hwm": None, "halted": False, "balances": {}}
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        data.setdefault("balances", {})
        return data
    except (OSError, ValueError):
        return {"hwm": None, "halted": False, "balances": {}}


def _rebalances(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("event") == "REBALANCE"]


def _latest_rebalance(rows: list[dict]) -> dict | None:
    rebs = _rebalances(rows)
    return rebs[-1] if rebs else None


def _latest_event(rows: list[dict], event: str) -> dict | None:
    for r in reversed(rows):
        if r.get("event") == event:
            return r
    return None


def _fg_label(fg: int | None) -> str:
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


def _explorer_base() -> str:
    return (
        "https://testnet.bscscan.com/tx/"
        if settings.agent_network == "bsc-testnet"
        else "https://bscscan.com/tx/"
    )


# --------------------------------------------------------------------------- #
# Card builders (each maps to one endpoint; all return plain dicts)
# --------------------------------------------------------------------------- #
def health_card() -> dict:
    from ictbot.runtime import heartbeat, kill_switch

    age = heartbeat.age_seconds()
    last = heartbeat.last_beat()
    last_iso = (
        datetime.fromtimestamp(last, tz=timezone.utc).isoformat() if last is not None else None
    )
    return {
        "ok": True,
        "heartbeat_age_s": round(age, 1) if age is not None else None,
        "last_beat_iso": last_iso,
        "mode": settings.twak_mode,
        # Which track the dashboard is reading, and whether it disagrees with the
        # agent's execution mode (e.g. viewing the SIM journal while trading LIVE).
        "journal_mode": settings.dashboard_journal,
        "journal_mismatch": settings.dashboard_journal != settings.twak_mode,
        "live_trading_enabled": bool(settings.enable_live_trading),
        "kill_switch_engaged": kill_switch.is_engaged(),
    }


def identity_card() -> dict | None:
    """ERC-8004 profile (key-free; no chain access)."""
    try:
        from ictbot.agent.identity import profile

        return profile()
    except Exception:
        return None


def strategy_card(rows: list[dict] | None = None) -> dict | None:
    try:
        from ictbot.agent.strategy_spec import load_spec, summary

        params, floor, ceiling = load_spec()
        from ictbot.runtime import active_tokens
        from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

        active = active_tokens.load()
        # Which registered strategy is running. Prefer the arm that ACTUALLY produced the latest
        # journaled tick (seeded into the Render image → env-independent: the dashboard reflects
        # what ran even when the serving process lacks the STRATEGY_NAME env). Fall back to the
        # configured default (STRATEGY_NAME, else the ALLOC_ADAPTIVE-derived locked default).
        rows = read_journal() if rows is None else rows
        journaled = (_latest_rebalance(rows) or {}).get("strategy")
        name = (
            journaled
            or settings.strategy_name
            or ("momentum_adaptive" if settings.alloc_adaptive else "momentum")
        )
        # The locked momentum default keeps its judge-facing config summary
        # (config/strategy.md); a non-default strategy uses the registry one-liner.
        summ = summary(n_tokens=len(active))
        if name not in ("momentum", "momentum_adaptive"):
            try:
                from ictbot.strategy import registry

                summ = registry.get(name).summary(params, n_tokens=len(active))
            except Exception:
                pass
        return {
            "name": name,
            "summary": summ,
            "tokens": list(CONTEST_TOKENS),
            "active": active,
            "params": {
                "top_k": params.top_k,
                "lookback": params.lookback,
                "cap_floor": floor,
                "cap_ceiling": ceiling,
                "rebal_bars": params.rebal_bars,
            },
        }
    except Exception:
        return None


_DEFAULT_LIVE = "momentum_adaptive"  # fallback live arm when nothing else resolves


def _live_arm_with_source() -> tuple[str, str]:
    """The arm the LIVE/contest tick runs, + where we learned it — env-independent so it's correct on
    the key-free Render deploy. Prefer the latest `allocator_live.jsonl` REBALANCE `strategy` (seeded
    into the image once live ticks run), else the configured `STRATEGY_NAME`, else the
    `ALLOC_ADAPTIVE`-derived default. Always reads the LIVE ledger (not the DASHBOARD_JOURNAL-selected
    track), so the live arm is right even while the dashboard is showing the sim track. Mirrors how
    `strategy_card` resolves the *running* arm."""
    live_jpath = JOURNAL_DIR / "allocator_live.jsonl"
    try:
        if live_jpath.exists():
            for line in reversed(live_jpath.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if row.get("event") == "REBALANCE" and row.get("strategy"):
                    return str(row["strategy"]), "live journal"
    except OSError:
        pass
    if settings.strategy_name:
        return settings.strategy_name, "configured (STRATEGY_NAME)"
    return (_DEFAULT_LIVE if settings.alloc_adaptive else "momentum"), "default"


def _live_arm() -> str:
    return _live_arm_with_source()[0]


def _readiness_verdict(
    name: str,
    survival: dict | None,
    stability: dict | None,
    forward: dict | None,
    live_arm: str = _DEFAULT_LIVE,
) -> dict:
    """Fuse the three signals into ONE contest-readiness verdict for the dashboard.

    Inlined mirror of scripts/contest_readiness._readiness (kept here so the API process
    never imports scripts/ — campaign/forward_promote — into the per-poll read path).
    Never auto-promotes; READY just means all automated gates cleared (human sign-off
    is still the final step). `live_arm` is the REAL live arm (derived, not hardcoded) so the
    Strategy Lab marks whichever arm is actually trading as 🔒 LIVE."""
    if name == live_arm:
        return {"state": "incumbent", "note": "live contest arm"}
    sv_pass = bool(survival and survival.get("passed"))
    grade = (stability or {}).get("grade")
    if not sv_pass:
        return {"state": "not_ready", "note": "survival failed"}
    if grade == "UNSTABLE":
        return {"state": "not_ready", "note": "stability UNSTABLE"}
    if forward and forward.get("forward_eligible"):
        return {"state": "ready", "note": "all gates cleared"}
    note = "forward not yet" if (forward or {}).get("status") == "evaluated" else "forward accruing"
    return {"state": "in_progress", "note": note}


def strategies_card() -> dict:
    """The registry menu + persisted verdicts + the current SIM selection — powers the
    dashboard strategy selector. SIM-only: `current` is what the SIM track runs; LIVE
    is operator-controlled and unaffected (enforced in run_allocator)."""
    from ictbot.runtime import stability_grades, strategy_select, verdicts
    from ictbot.strategy import registry
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    default = settings.strategy_name or (
        "momentum_adaptive" if settings.alloc_adaptive else "momentum"
    )
    current = strategy_select.load(default)
    live_arm = _live_arm()  # the REAL live arm (journal/STRATEGY_NAME-derived) → marked 🔒 LIVE
    vmap = verdicts.load()
    gmap = stability_grades.load()  # robust/fragile/unstable grades (make stability)
    items = []
    for name in registry.available():
        try:
            summ = registry.get(name).summary(
                registry.get(name).default_params(), n_tokens=len(CONTEST_TOKENS)
            )
        except Exception:
            summ = name
        alias_of = registry.alias_target(name)
        # An alias inherits its target arm's verdict/grade when it has none of its own (the
        # logic is identical, so re-validating under the alias name is unnecessary).
        v = vmap.get(name) or (vmap.get(alias_of) if alias_of else None) or {}
        stab = gmap.get(name) or (gmap.get(alias_of) if alias_of else None)
        survival, forward = v.get("survival"), v.get("forward")
        items.append(
            {
                "name": name,
                "summary": summ,
                "current": name == current,
                "alias_of": alias_of,
                "survival": survival,
                "forward": forward,
                "stability": stab,
                # SCOREBOARD (backtest perf) — pass-through of the persisted `perf`; never an edge claim.
                "scoreboard": v.get("perf"),
                "readiness": _readiness_verdict(name, survival, stab, forward, live_arm=live_arm),
            }
        )
    return {"items": items, "current": current}


def live_arm_card() -> dict:
    """The arm actually trading real money (or configured to) + its survival/forward gate — distinct
    from the SIM Strategy Lab. Operator-switchable (`live_tick.sh <arm>` / run_allocator `--strategy`);
    the dashboard cannot change it. Read-only, key-free."""
    from ictbot.runtime import verdicts
    from ictbot.strategy import registry

    name, source = _live_arm_with_source()
    v = verdicts.load().get(name) or {}
    try:
        candle_source = getattr(registry.get(name), "candle_source", "cmc_4h")
    except Exception:
        candle_source = None
    return {
        "name": name,
        "source": source,
        "candle_source": candle_source,
        "survival": v.get("survival"),
        "forward": v.get("forward"),
        "switchable": True,
        "note": "operator-switchable via `live_tick.sh <arm>` (survival-gated); the dashboard can't change it",
    }


def state_card(rows: list[dict] | None = None) -> dict:
    rows = read_journal() if rows is None else rows
    state = read_state()
    latest = _latest_rebalance(rows)
    nav = latest.get("nav_after") if latest else (state.get("hwm") or settings.alloc_start_usdt)
    weights = (latest.get("weights_after") or {}) if latest else {}
    # Halt reason/time — surfaced from the latest DD_HALT row so the dashboard can
    # show WHY the agent stopped, not just that it did.
    halted = bool(state.get("halted"))
    halt = _latest_event(rows, "DD_HALT")
    halt_reason = halt_ts = None
    if halted:
        if halt and halt.get("dd") is not None:
            halt_reason = (
                f"drawdown {halt['dd'] * 100:.1f}% > cap {(halt.get('dd_cap') or 0) * 100:.0f}%"
            )
            halt_ts = halt.get("ts")
        else:
            halt_reason = "drawdown halt"
    # Trades toward the >=7 contest floor (prefer the latest rebalance row, which
    # journals the running count; fall back to state).
    cum = (latest or {}).get("cumulative_swaps")
    if cum is None:
        cum = state.get("cumulative_swaps", 0)
    # PnL-campaign profit-lock status — surfaced from the persisted state (authoritative)
    # so the dashboard can show "ARMED +X%" / "PROFIT LOCKED". Derived from the state
    # file (not settings) so it works zero-secret on the cloud: a campaign anchor in the
    # state IS the signal the campaign is live. None when no anchor was ever set.
    profit_lock = None
    anchor = state.get("campaign_start_nav")
    if anchor:
        profit_lock = {
            "armed": bool(state.get("profit_lock_armed")),
            "locked": bool(state.get("profit_locked")),
            "campaign_start_nav": anchor,
            "cum_ret": round(nav / anchor - 1.0, 4) if (nav and anchor) else None,
            "peak_since_trigger": state.get("peak_since_trigger"),
            "lock_floor": state.get("lock_floor"),
        }
    return {
        "hwm": state.get("hwm"),
        "halted": halted,
        "halt_reason": halt_reason,
        "halt_ts": halt_ts,
        "nav": nav,
        "balances": state.get("balances") or {},
        "weights": weights,
        "cumulative_swaps": int(cum or 0),
        "trade_floor": int((latest or {}).get("trade_floor_min", settings.trade_floor_min)),
        "profit_lock": profit_lock,
    }


def nav_card(rows: list[dict] | None = None) -> dict:
    rows = read_journal() if rows is None else rows
    rebs = _rebalances(rows)
    curve = [
        {"ts": r["ts"], "nav": r.get("nav_after")} for r in rebs if r.get("nav_after") is not None
    ]
    dd_series = [{"ts": r["ts"], "dd": float(r.get("dd_from_hwm") or 0.0)} for r in rebs]
    state = read_state()
    fallback_nav = state.get("hwm") or settings.alloc_start_usdt
    return {
        "curve": curve,
        "current_nav": curve[-1]["nav"] if curve else fallback_nav,
        "hwm": state.get("hwm") or fallback_nav,
        "drawdown": {
            "current": dd_series[-1]["dd"] if dd_series else 0.0,
            "series": dd_series,
        },
        "caps": {"team": 0.15, "dq": 0.30, "configured": settings.max_drawdown_frac},
    }


def _fear_greed_with_fallback(latest: dict | None) -> tuple[int | None, bool]:
    """Prefer the F&G already in the latest journal row (zero network). Only fall
    back to a TTL-cached live CMC call. Returns (value, stale)."""
    if latest and latest.get("fear_greed") is not None:
        return int(latest["fear_greed"]), False
    now = time.time()
    if now - _fg_cache["ts"] < _FG_TTL_S:
        return _fg_cache["value"], _fg_cache["value"] is not None
    try:
        from ictbot.data.cmc import fear_greed

        val = fear_greed(settings.cmc_api_key or None)
    except Exception:
        val = None
    _fg_cache.update(value=val, ts=now)
    return val, True


# A journaled tick's regime read (F&G / regime_score / deploy_cap) is the value AT that tick, not a live
# market read. Past one rebalance cadence (24h) + grace, flag it stale so the dashboard's "CACHED F&G" cue
# is honest even when a live-F&G fallback wasn't needed. Mirrors web/src/lib/freshness.ts STALE_AFTER_S.
# A slow-changing boolean (flips once at the threshold) — safe under the /api/snapshot ETag + micro-cache.
_REGIME_STALE_AFTER_S = 28 * 3600


def _tick_age_s(latest: dict | None) -> float | None:
    """Age in seconds of a rebalance row's ISO `ts`, or None if absent/unparseable."""
    ts = (latest or {}).get("ts")
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds()


def regime_card(rows: list[dict] | None = None) -> dict:
    rows = read_journal() if rows is None else rows
    latest = _latest_rebalance(rows)
    fg, stale = _fear_greed_with_fallback(latest)
    # The regime read reflects the latest tick; if that tick is older than the rebalance cadence, mark it
    # stale so the UI shows "CACHED F&G" rather than implying a current live market read.
    age = _tick_age_s(latest)
    if age is not None and age > _REGIME_STALE_AFTER_S:
        stale = True
    return {
        "regime_score": (latest or {}).get("regime_score"),
        "fear_greed": fg,
        "fear_greed_label": _fg_label(fg),
        "deploy_cap": (latest or {}).get("deploy_cap"),
        "stale": stale,
    }


def rebalances_card(n: int = 10, rows: list[dict] | None = None) -> dict:
    rows = read_journal() if rows is None else rows
    base = _explorer_base()
    items = []
    for r in _rebalances(rows)[-n:][::-1]:  # newest-first
        items.append(
            {
                "ts": r.get("ts"),
                "event": r.get("event", "REBALANCE"),
                "mode": r.get("mode", "sim"),
                "strategy": r.get("strategy"),  # which registered strategy produced this tick
                "candle_source": r.get("candle_source"),  # data provenance (None in old rows)
                "quote_source": r.get("quote_source"),  # 7d-tilt source: cmc_ws | rest | None
                "onchain_signals": r.get("onchain_signals"),  # CMC on-chain DEX signals (or None)
                "nav_before": r.get("nav_before"),
                "nav_after": r.get("nav_after"),
                "n_swaps": r.get("n_swaps", 0),
                "n_swaps_total": r.get("n_swaps_total", r.get("n_swaps", 0)),
                "n_failed": r.get("n_failed", 0),
                "failed_swaps": r.get("failed_swaps") or [],
                "fees_usd": r.get("fees_usd", 0.0),
                "tx": [{"hash": h, "url": f"{base}{h}"} for h in (r.get("tx") or [])],
                "target": r.get("target") or {},
                "weights_after": r.get("weights_after") or {},
                "rationale": r.get("rationale"),
                "x402_dex": r.get("x402_dex"),  # pillar-1 per-tick CMC AI Agent Hub read (or None)
                "active_tokens": r.get(
                    "active_tokens"
                ),  # universe the tick ranked over (None = pre-toggle)
                "profit_lock": r.get(
                    "profit_lock"
                ),  # PnL-campaign ratchet status for this tick (or None)
            }
        )
    return {"items": items}


def rationale_card(n: int = 20, rows: list[dict] | None = None) -> dict:
    rows = read_journal() if rows is None else rows
    items = [
        {"ts": r.get("ts"), "rationale": r.get("rationale")}
        for r in _rebalances(rows)
        if r.get("rationale")
    ]
    return {"items": items[-n:][::-1]}  # newest-first


def token_rotation_card(rows: list[dict] | None = None) -> dict:
    """Per-token activity across the WHOLE journal — which of the contest universe have actually been
    traded — split honestly into two sources:
      • HELD   : appeared in a REBALANCE `weights_after` > 0 (a real momentum top-k holding), and
      • NUDGED : appeared in a FLOOR_NUDGE `tokens` (a ~0-NAV contest-floor round-trip that touches
                 the rest of the universe over the week).
    The momentum allocation only ever holds `top_k` (2) tokens; the floor rotation is what reaches the
    other six. This is NOT an edge claim — the nudges are deliberately ~0 NAV. Drives the dashboard
    'Token Rotation' card (N/8 touched, held vs floor-rotated, last-touched per token)."""
    rows = read_journal() if rows is None else rows
    from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

    EPS = 1e-9
    held: dict[str, dict] = {}  # token -> {"count": int, "last_ts": str|None}
    nudged: dict[str, dict] = {}
    for r in rows:
        ev, ts = r.get("event"), r.get("ts")
        if ev == "REBALANCE":
            for tok, w in (r.get("weights_after") or {}).items():
                if isinstance(w, (int, float)) and w > EPS:
                    e = held.setdefault(tok, {"count": 0, "last_ts": None})
                    e["count"] += 1
                    e["last_ts"] = ts
        elif ev == "FLOOR_NUDGE":
            for tok in r.get("tokens") or []:
                e = nudged.setdefault(tok, {"count": 0, "last_ts": None})
                e["count"] += 1
                e["last_ts"] = ts

    tokens, touched_count = [], 0
    for tok in CONTEST_TOKENS:
        h, ng = held.get(tok), nudged.get(tok)
        source = "both" if (h and ng) else "held" if h else "nudged" if ng else "none"
        touched = source != "none"
        touched_count += int(touched)
        last_ts = max(
            (t for t in ((h or {}).get("last_ts"), (ng or {}).get("last_ts")) if t),
            default=None,
        )
        tokens.append(
            {
                "token": tok,
                "touched": touched,
                "source": source,  # held | nudged | both | none
                "count": (h["count"] if h else 0) + (ng["count"] if ng else 0),
                "last_ts": last_ts,
            }
        )
    return {
        "tokens": tokens,
        "touched_count": touched_count,
        "total": len(CONTEST_TOKENS),
        "held": sorted(held),  # momentum holdings (real allocation)
        "nudged": sorted(nudged),  # contest-floor rotation (~0 NAV)
    }


# --------------------------------------------------------------------------- #
# Three-pillar status (CMC/x402 · TWAK · BNB-SDK/NodeReal) — best-effort, the
# only network reads (NodeReal RPC + Base USDC balance) are TTL-cached so the
# 4s snapshot poll stays fast and never blocks on a cold endpoint.
# --------------------------------------------------------------------------- #
_PILLARS_TTL_S = 60.0
_pillars_net_cache: dict = {"value": None, "ts": 0.0}


def _x402_receipts() -> dict:
    """Summarize data/x402/receipts.json (settled count + USDC spent). Zeros if absent."""
    out = {"total": 0, "settled": 0, "spent_usdc": 0.0, "last_ts": None, "last_status": None}
    try:
        rows = json.loads((DATA_DIR / "x402" / "receipts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(rows, list) or not rows:
        return out
    units = 0
    for r in rows:
        out["total"] += 1
        if r.get("status") == "settled":
            out["settled"] += 1
            try:
                units += int(r.get("value") or 0)
            except (TypeError, ValueError):
                pass
    out["spent_usdc"] = round(units / 1e6, 6)  # USDC is 6dp
    out["last_ts"] = rows[-1].get("ts")
    out["last_status"] = rows[-1].get("status")
    return out


def _provenance_card() -> dict | None:
    """vlayer on-chain data-provenance attestation for the SOLD report (read-only, key-free).

    `enabled` is ALWAYS present so the dashboard can distinguish 'off' from 'on but not yet proven'
    (pending state); the proof fields appear only once an attestation exists on-chain. Never raises
    and makes no network call when disabled — mirrors `_commerce_preview` / `regime_report`'s hook."""
    try:
        from ictbot.agent import provenance

        att = provenance.latest_attestation()  # None when disabled/unproven (best-effort)
        # `enabled` tracks the vlayer FLAG alone (not `available()`, which also needs a deployed
        # verifier address) so the dashboard shows the honest "on · attestation pending" state as
        # soon as the integration is switched on — the proof fields still appear only once a real
        # on-chain attestation exists (`att`). Never fabricates a proof.
        return {"enabled": bool(settings.vlayer_enabled), **(att or {})}
    except Exception:
        return None


def _commerce_jobs() -> dict:
    """Summarize data/journal/commerce_jobs.jsonl — the ERC-8183 PROVIDER ledger (the agent
    SELLING its Market Regime Report to other agents). Walks the event stream (CREATE / FUND /
    SUBMIT / SUBMITTED_ONCHAIN / SETTLE) into per-job state. Zeros if absent. Read-only.

    `jobs_served` = distinct jobs the agent actually delivered on-chain (SUBMITTED_ONCHAIN);
    `revenue_u`  = Σ FUND.amount of served jobs / 1e18 (payment token "U" is 18dp)."""
    out = {
        "enabled": bool(settings.erc8183_enabled),
        "network": settings.erc8183_network,
        "jobs_created": 0,
        "jobs_funded": 0,
        "jobs_served": 0,
        "jobs_settled": 0,
        "jobs_pending_settle": 0,
        "last_settle_status": None,
        "revenue_u": 0.0,
        "last_ts": None,
        "last_event": None,
        "last_deliverable_hash": None,
        "last_deliverable_url": None,
        "last_tx": None,
    }
    try:
        lines = (
            (DATA_DIR / "journal" / "commerce_jobs.jsonl").read_text(encoding="utf-8").splitlines()
        )
    except (OSError, ValueError):
        return out
    jobs: dict = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except (ValueError, TypeError):
            continue
        jid = r.get("job_id")
        ev = r.get("event")
        if jid is not None:
            j = jobs.setdefault(str(jid), {})
            if ev == "FUND":
                try:
                    j["amount"] = int(r.get("amount") or 0)
                except (TypeError, ValueError):
                    pass
            elif ev == "SUBMITTED_ONCHAIN":
                j["served"] = True
                if r.get("deliverable_hash"):
                    out["last_deliverable_hash"] = r.get("deliverable_hash")
                if r.get("deliverable_url"):
                    out["last_deliverable_url"] = r.get("deliverable_url")
                if r.get("tx"):
                    out["last_tx"] = r.get("tx")
            elif ev == "SETTLE":
                j["settled"] = True
                out["last_settle_status"] = "settled"  # chronological walk → latest attempt wins
            elif ev == "SETTLE_DEFERRED":
                # served but the optimistic dispute window was still open at the last attempt
                out["last_settle_status"] = "deferred"
            if ev == "CREATE":
                j["created"] = True
            if ev == "FUND":
                j["funded"] = True
        out["last_ts"] = r.get("ts") or out["last_ts"]
        out["last_event"] = ev or out["last_event"]
    out["jobs_created"] = sum(1 for j in jobs.values() if j.get("created"))
    out["jobs_funded"] = sum(1 for j in jobs.values() if j.get("funded"))
    out["jobs_served"] = sum(1 for j in jobs.values() if j.get("served"))
    out["jobs_settled"] = sum(1 for j in jobs.values() if j.get("settled"))
    # served on-chain but not yet finalized — the honest "awaiting finalization" count
    out["jobs_pending_settle"] = sum(
        1 for j in jobs.values() if j.get("served") and not j.get("settled")
    )
    units = sum(int(j.get("amount") or 0) for j in jobs.values() if j.get("served"))
    out["revenue_u"] = round(units / 1e18, 8)  # payment token "U" is 18dp
    return out


def _commerce_service(net: dict, link: dict) -> dict:
    """The ERC-8183 service the agent ADVERTISES — what it sells, anchored to its ERC-8004
    identity. Key-free: reuses the SAME public values the `nodereal` pillar derives (no private
    key / wallet password). The capability is real even before the first job settles, so the
    panel shows the genuine offering rather than a blank."""
    try:
        from ictbot.agent.identity import COMMERCE_CAPABILITIES

        caps = list(COMMERCE_CAPABILITIES)
    except Exception:
        caps = []
    return {
        "name": "Market Regime Report",
        "report_schema": "cmc-regime-report/v1",
        "price": int(settings.erc8183_service_price or 0),
        "storage": settings.erc8183_storage,
        "capabilities": caps,
        "provider": net.get("pay_wallet"),
        "agent_id": int(settings.agent_id or 0),
        "registry": link.get("registry"),
    }


def _commerce_preview(rows: list[dict]) -> dict | None:
    """A live preview of the deliverable the agent would sell RIGHT NOW — the genuine product,
    NOT a recompute: sourced from the latest allocator tick (regime read + momentum ranking +
    rationale), reusing `regime_card` / `_latest_rebalance`. Returns None until the first
    rebalance exists (panel degrades). Public market data only — no secrets."""
    latest = _latest_rebalance(rows)
    if not latest:
        return None
    reg = regime_card(rows)
    ranking_src = latest.get("target") or latest.get("weights_after") or {}
    ranking = sorted(ranking_src, key=lambda k: ranking_src.get(k) or 0.0, reverse=True)[:6]
    rationale = latest.get("rationale")
    if isinstance(rationale, str) and len(rationale) > 180:
        rationale = rationale[:180].rstrip() + "…"
    return {
        "ts": latest.get("ts"),
        "strategy": latest.get("strategy"),
        "regime_score": reg.get("regime_score"),
        "deploy_cap": reg.get("deploy_cap"),
        "fear_greed": reg.get("fear_greed"),
        "fear_greed_label": reg.get("fear_greed_label"),
        "momentum_ranking": ranking,
        "rationale": rationale,
    }


def _commerce_can_create() -> bool:
    """Whether a LOCAL operator run can sign BOTH sides (provider + a distinct buyer keystore) — gates
    the dashboard 'create job' button. Key-free boolean (no wallet built); False on the read-only
    deploy, which has no signing password."""
    try:
        from ictbot.agent import commerce

        return bool(commerce.buyer_available())
    except Exception:
        return False


def _pillars_net() -> dict:
    """Network-dependent pillar bits (identity wallet, NodeReal link, Base USDC
    balance), TTL-cached. Each piece is independently guarded — a cold RPC degrades
    that field to None, never the whole card."""
    now = time.time()
    cached = _pillars_net_cache["value"]
    if cached is not None and now - _pillars_net_cache["ts"] < _PILLARS_TTL_S:
        return cached
    net: dict = {
        "pay_wallet": None,
        "link": None,
        "base_usdc_balance": None,
        "sdk_installed": False,
        "identity_wallet_bnb": None,
    }
    try:
        from ictbot.agent import identity

        net["sdk_installed"] = identity.available()
        # display_address() prefers the PUBLIC AGENT_IDENTITY_ADDRESS, so the deployed
        # read-only dashboard needs NO private key / wallet password to show the wallet.
        net["pay_wallet"] = identity.display_address()
        # Identity-wallet BNB — the direct-gas heartbeat funding source. Surfaces whether pillar-3
        # heartbeats CAN land (0 BNB = the broken state). Read-only; TTL-cached with the rest.
        net["identity_wallet_bnb"] = identity.identity_wallet_bnb(net["pay_wallet"])
        if settings.nodereal_api_key:
            net["link"] = identity.verify_paymaster_link()  # current AGENT_NETWORK
    except Exception:
        pass
    try:
        if settings.x402_enabled and net["pay_wallet"]:
            from ictbot.data.x402_cmc import base_usdc_balance

            net["base_usdc_balance"] = base_usdc_balance(net["pay_wallet"])
    except Exception:
        pass
    _pillars_net_cache.update(value=net, ts=now)
    return net


def pillars_card(rows: list[dict] | None = None) -> dict:
    """Status of all three agent pillars for the dashboard. Best-effort; degrades
    cleanly when a pillar isn't configured. Network reads are TTL-cached (60s)."""
    rows = read_journal() if rows is None else rows
    latest = _latest_rebalance(rows)
    net = _pillars_net()
    link = net.get("link") or {}
    return {
        # Pillar 1 — CMC AI Agent Hub (x402 paid data)
        "cmc": {
            "x402_enabled": bool(settings.x402_enabled),
            "pay_wallet": net.get("pay_wallet"),
            "base_usdc_balance": net.get("base_usdc_balance"),
            "receipts": _x402_receipts(),
            "last_dex": (latest or {}).get("x402_dex"),
        },
        # Pillar 2 — Trust Wallet / TWAK (execution)
        "twak": {
            "mode": settings.twak_mode,
            "gasless": bool(settings.twak_gasless),
            "gasless_flag": settings.twak_gasless_flag,
            "cumulative_swaps": int((latest or {}).get("cumulative_swaps", 0) or 0),
            "trade_floor": int((latest or {}).get("trade_floor_min", settings.trade_floor_min)),
        },
        # Pillar 3 — on-chain agent SDK (bnbagent) + NodeReal/MegaFuel (identity + gasless)
        "nodereal": {
            "api_key_set": bool(settings.nodereal_api_key),
            "network": settings.agent_network,
            "sdk_installed": net.get("sdk_installed"),
            "use_paymaster": bool(settings.agent_use_paymaster),
            "reachable": link.get("reachable"),
            "chain_id": link.get("chain_id"),
            "chain_ok": link.get("chain_ok"),
            "sponsorable": link.get("sponsorable"),
            "wallet": link.get("wallet") or net.get("pay_wallet"),
            "nonce": link.get("nonce"),
            "registry": link.get("registry"),
            "note": link.get("note"),
            "agent_id": int(settings.agent_id or 0),
            "heartbeat_enabled": bool(settings.agent_heartbeat_enabled),
            # Heartbeat HEALTH — is pillar 3 actually alive? `identity_wallet_bnb` shows the
            # direct-gas funding (0 = the broken state); last_heartbeat_* comes from the latest
            # tick's journaled `heartbeat` result (key-free on Render, no on-chain read needed).
            "identity_wallet_bnb": net.get("identity_wallet_bnb"),
            "last_heartbeat_ok": ((latest or {}).get("heartbeat") or {}).get("ok"),
            "last_heartbeat_tx": ((latest or {}).get("heartbeat") or {}).get("tx"),
            "last_heartbeat_ts": (latest or {}).get("ts")
            if (latest or {}).get("heartbeat")
            else None,
            # WHY the last heartbeat failed (e.g. MegaFuel 403 / sponsor unset / insufficient gas) —
            # so the IdentityCard shows the reason, not just "failing". Public, never a secret.
            "last_heartbeat_error": ((latest or {}).get("heartbeat") or {}).get("error"),
        },
        # SDK prize — ERC-8183 agentic commerce: the SELL side (agent monetizes its CMC analysis).
        # Beyond the job ledger, surface the REAL advertised service + a live deliverable preview so
        # the capability is visible before the first on-chain job settles (no seeded/fake jobs).
        "commerce": {
            **_commerce_jobs(),
            "service": _commerce_service(net, link),
            "preview": _commerce_preview(rows),
            "can_create": _commerce_can_create(),
            "provenance": _provenance_card(),
        },
    }


def wallet_card() -> dict:
    """LIVE on-chain holdings of the trading wallet — the "real funds" card that sits
    beside the SIM journal NAV. Lazy-imports the web3 path so the journal-only reads
    stay light; the read itself is TTL-cached + never raises (see api/onchain.py)."""
    from ictbot.api import onchain

    return onchain.wallet_card()


# --------------------------------------------------------------------------- #
# Market intelligence (CMC Startup tier) + CMC API telemetry. The live intel read is
# TTL-cached (300s) so the 4s poll never drives a CMC fetch; regime_terms come from the
# latest journal row (zero network). Both degrade cleanly when intel is disabled.
# --------------------------------------------------------------------------- #
_INTEL_TTL_S = 300.0
_intel_net_cache: dict = {"value": None, "ts": 0.0}


def _market_intel_net() -> dict | None:
    now = time.time()
    cached = _intel_net_cache["value"]
    if cached is not None and now - _intel_net_cache["ts"] < _INTEL_TTL_S:
        return cached
    snap = None
    try:
        from ictbot.data.cmc_intel import market_intel_snapshot

        snap = market_intel_snapshot()
    except Exception:
        snap = None
    _intel_net_cache.update(value=snap, ts=now)
    return snap


def market_intel_card(rows: list[dict] | None = None) -> dict:
    """CMC market intelligence: live global metrics + F&G trend + movers + categories,
    plus the regime-term breakdown from the latest journal row. Live pieces are None/[]
    when CMC_INTEL_ENABLED is off (the panel degrades to the journal's regime terms)."""
    rows = read_journal() if rows is None else rows
    latest = _latest_rebalance(rows)
    snap = _market_intel_net() or {}
    return {
        "enabled": bool(settings.cmc_intel_enabled),
        "global_metrics": snap.get("global"),
        "fng_trend": snap.get("fng_trend") or [],
        "movers": snap.get("movers") or {"gainers": [], "losers": []},
        "categories": snap.get("categories") or [],
        "regime_terms": (latest or {}).get("regime_terms"),
    }


def cmc_api_card() -> dict:
    """CMC client telemetry (credit budget + rate-limit) — reads the ledger, no network."""
    try:
        from ictbot.data.cmc_client import CMC

        return CMC.telemetry()
    except Exception:
        return {}


def market_data_hub_card() -> dict | None:
    """FREE 'Market Data Hub' — the live free-data-stack exhibit that replaces the (dead) CMC Agent
    Hub on the dashboard. Read-only, key-free, never-raise; always emits `enabled` (mirrors
    `_provenance_card`). `overview` = the free composed market-overview (regime, F&G, BTC dominance,
    mktcap 24h Δ, narratives); `dex` = per-token DexScreener signals. None when free data is off."""
    if not settings.free_data:
        return None
    try:
        from ictbot.data import dexscreener, free_overview
        from ictbot.strategy.momentum_allocator import CONTEST_TOKENS

        return {
            "enabled": True,
            "overview": free_overview.free_market_overview(),
            "dex": dexscreener.dex_signals(list(CONTEST_TOKENS), with_windows=True),
            "sources": ["binance", "alternative.me", "dexscreener", "coingecko"],
        }
    except Exception:
        return None


def agent_hub_card(rows: list[dict] | None = None) -> dict:
    """CMC Agent Hub exhibit (the 'Best Use of CoinMarketCap' panel): the live
    market-overview SKILL read + TA the agent acted on (from the latest journal row), the
    Data MCP call counts, and the x402 pay-per-call receipts. All read-only / from disk."""
    rows = read_journal() if rows is None else rows
    latest = _latest_rebalance(rows) or {}
    try:
        from ictbot.data import cmc_agent_hub

        mcp = cmc_agent_hub.telemetry()
        tools_available = list(cmc_agent_hub.MCP_TOOLS)
    except Exception:
        mcp = {"enabled": False, "calls": 0, "by_tool": {}}
        tools_available = []
    return {
        # Telemetry-aware: the read-only dashboard mirrors the agent's REAL (seeded) MCP
        # activity, so the exhibit shows whenever there's telemetry to show — not only when
        # this serving process happens to carry the CMC_MCP_ENABLED flag. (Display only; the
        # real trade/skill gate `settings.cmc_mcp_enabled` is untouched.)
        "mcp_enabled": bool(settings.cmc_mcp_enabled)
        or bool(mcp.get("calls") or mcp.get("by_tool")),
        "ta_enabled": bool(settings.alloc_ta_enabled),
        "skill_enabled": bool(settings.cmc_skill_regime),
        "mcp": {
            "calls": mcp.get("calls", 0),
            "by_tool": mcp.get("by_tool", {}),
            # Full Data-MCP catalog (12) so the panel shows exercised/available, not just called.
            "tools_available": tools_available,
            "last_call_ts": mcp.get("last_call_ts"),
        },
        "ta_health": latest.get("ta_health"),
        "ta_source": latest.get("ta_source"),
        "skill": latest.get("cmc_skill"),
        "x402": _x402_receipts(),
        "x402_enabled": bool(settings.x402_enabled),
        # On-chain WebSocket signals the agent harvested this tick — read from the JOURNAL row so it
        # renders on Render (the live cmc_stream cache isn't on the deployed disk).
        "onchain": latest.get("onchain_signals"),
        "onchain_enabled": bool(settings.cmc_onchain_enabled)
        or bool(latest.get("onchain_signals")),
        # CMC-native rotation levers the agent acted on this tick (sector rotation + multi-window
        # momentum). Read from the JOURNAL row so it renders on Render. None unless a lever is on.
        "rotation": latest.get("cmc_rotation"),
        "rotation_enabled": bool(settings.alloc_sector_tilt or settings.alloc_mom_multi_w)
        or bool(latest.get("cmc_rotation")),
    }


def agent_hub_ping() -> dict:
    """LIVE on-demand probe of CMC's Agent Hub — proves the MCP + composed Skill genuinely work on
    THIS server (not seeded snapshot data). Makes real outbound calls at request time: a live MCP
    `tools/list` + a sample `tools/call` (ping), and a fresh `market_overview()` (the composed
    Skill → risk budget). Never raises; returns `enabled:false` if the server has no key / MCP is
    off. Button-triggered only (not on the snapshot poll); underlying tool calls are TTL-cached."""
    out = {
        "enabled": False,
        "tools_live": 0,
        "sample_ok": False,
        "last_error": None,
        "ts": datetime.now(timezone.utc).isoformat(),
        "skill": None,
    }
    try:
        from ictbot.data import cmc_agent_hub

        p = cmc_agent_hub.ping() or {}
        out.update(
            enabled=bool(p.get("enabled")),
            tools_live=int(p.get("tools_live") or 0),
            sample_ok=bool(p.get("sample_ok")),
            last_error=p.get("last_error"),
        )
        if out["enabled"]:
            mo = cmc_agent_hub.market_overview()  # the live composed Skill (TTL-cached tool reads)
            if mo:
                out["skill"] = {
                    "risk_budget": mo.get("risk_budget"),
                    "regime": mo.get("regime"),
                    "headline": mo.get("headline"),
                    "tools_used": mo.get("tools_used") or [],
                }
    except Exception as e:  # noqa: BLE001 — read-only probe must never 500 the dashboard
        out["last_error"] = f"{type(e).__name__}: {str(e)[:80]}"
    return out


# Commerce revenue is denominated in the ERC-8183 escrow token "U", which has no price oracle in-repo.
# U is stablecoin-like, so we value it at $1 for the consolidated total and SAY SO in the UI (a tiny,
# explicitly-flagged assumption — revenue is ~0.2 U today, far below trading-NAV scale).
_U_USD = 1.0


def economy_card() -> dict:
    """The agent's economic position. The HEADLINE is the TRADING PnL only (the book's NAV − anchor) —
    the honest 'is the strategy making money' number. Reads the LIVE track EXPLICITLY (not the
    DASHBOARD_JOURNAL selection) so it reflects real on-chain money regardless of which track renders.

        net_economic_usd = trading_pnl_usd          # headline = TRADING PnL ONLY

    x402 spend and commerce are REAL on-chain activity but are NOT trading results, so they are shown as
    SEPARATE context lines and never summed into the headline:
      - trading_pnl_usd     : LIVE NAV − campaign_start_nav (already net of swap fees + BNB gas). HEADLINE.
      - x402_spent_usd      : CMC data cost (USDC on Base) — an OPERATING expense paid from the identity
                              wallet, not the trading book. Context only; excluded from PnL.
      - commerce_revenue_usd: ERC-8183 'U' from SELF-FUNDED jobs (buyer + provider are both operator
                              wallets) — an on-chain integration PROOF that nets to ~−gas, NOT external
                              revenue. Context only; excluded from PnL.
    """
    # LIVE trading book — explicit live track (env-independent), mirrors _live_arm_with_source.
    current_nav = anchor = None
    live_journal = JOURNAL_DIR / "allocator_live.jsonl"
    if live_journal.exists():
        try:
            for line in reversed(live_journal.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if row.get("event") == "REBALANCE" and row.get("nav_after") is not None:
                    current_nav = float(row["nav_after"])
                    break
        except OSError:
            pass
    try:
        live_state = json.loads(
            (JOURNAL_DIR / "allocator_live_state.json").read_text(encoding="utf-8")
        )
        anchor = live_state.get("campaign_start_nav")
        if current_nav is None:
            current_nav = live_state.get("hwm")
    except (OSError, ValueError):
        pass
    trading_pnl_usd = (
        round(float(current_nav) - float(anchor), 2)
        if current_nav is not None and anchor is not None
        else None
    )

    revenue_u = float(_commerce_jobs().get("revenue_u") or 0.0)
    commerce_revenue_usd = round(revenue_u * _U_USD, 2)
    x402_spent_usd = round(float(_x402_receipts().get("spent_usdc") or 0.0), 2)

    # Headline = TRADING PnL only. x402 + commerce are shown separately, NEVER summed into the headline.
    net_economic_usd = trading_pnl_usd
    return {
        "trading_pnl_usd": trading_pnl_usd,
        "trading_nav_usd": round(float(current_nav), 2) if current_nav is not None else None,
        "anchor_usd": round(float(anchor), 2) if anchor is not None else None,
        "commerce_revenue_u": round(revenue_u, 8),
        "commerce_revenue_usd": commerce_revenue_usd,
        "commerce_self_funded": True,
        "x402_spent_usd": x402_spent_usd,
        # net == trading PnL: the headline is strategy performance only (context lines below are excluded).
        "net_economic_usd": net_economic_usd,
        "u_valuation_note": "U valued at $1 (stablecoin-like; price oracle pending)",
        "gas_note": "net-of-gas: swap gas already in trading NAV (BNB); ERC-8004 heartbeat paymaster-sponsored",
        "commerce_note": ("self-funded — buyer + provider are both operator wallets; an on-chain ERC-8183 "
                          "integration proof that nets to ~−gas, NOT external revenue (excluded from PnL)"),
        "x402_note": ("CMC data cost (USDC on Base) — an operating expense from the identity wallet, "
                      "not a trading result (excluded from PnL)"),
    }


def scheduler_card() -> dict:
    """Scheduler-health signal so a SILENT cron death is caught in an hour, not days (it once ran dark
    for days). Pure read of artifacts the jobs already write: the LIVE journal's newest REBALANCE ts
    (is the daily live tick still firing?) and the dd_watch log's newest 'dd-watch OK' ts (is the
    intraday drawdown safety-net running?). `stale` past a generous threshold → the dashboard/arm_check
    flag it red. Never raises; unknown ages degrade to stale (fail-loud, not fail-silent)."""
    import re

    now = datetime.now(timezone.utc)

    def _age_s(ts: str):
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return (now - t).total_seconds()
        except Exception:
            return None

    live_age_s = None
    try:
        lj = JOURNAL_DIR / "allocator_live.jsonl"
        if lj.exists():
            rows = [json.loads(x) for x in lj.read_text(encoding="utf-8").splitlines() if x.strip()]
            rebs = [r for r in rows if r.get("event") == "REBALANCE" and r.get("ts")]
            if rebs:
                live_age_s = _age_s(rebs[-1]["ts"])
    except Exception:
        pass

    dd_age_s = None
    try:
        log = DATA_DIR / "logs" / "dd_watch_live.log"
        if log.exists():
            oks = [ln for ln in log.read_text(encoding="utf-8").splitlines() if "dd-watch OK" in ln]
            if oks:
                m = re.search(r"\[([0-9T:\-+.Z]+)\]", oks[-1])
                if m:
                    dd_age_s = _age_s(m.group(1))
    except Exception:
        pass

    live_stale = live_age_s is None or live_age_s > 26 * 3600   # daily tick → 26h grace
    dd_stale = dd_age_s is None or dd_age_s > 60 * 60           # 30-min cadence → 60m grace
    return {
        "ok": not (live_stale or dd_stale),
        "live_tick_age_h": round(live_age_s / 3600, 1) if live_age_s is not None else None,
        "live_tick_stale": live_stale,
        "dd_watch_age_m": round(dd_age_s / 60, 1) if dd_age_s is not None else None,
        "dd_watch_stale": dd_stale,
        "note": ("scheduler healthy" if not (live_stale or dd_stale)
                 else "STALE — a cron job may have stopped (grant Full Disk Access to /usr/sbin/cron)"),
    }


def evaluation_card() -> dict | None:
    """Forward-gated auto-selector verdict for the dashboard — the recommended LIVE arm + the ranked
    risk-adjusted scores + the anti-chasing hysteresis state. Reads the evaluator's recommendation file
    (data/reports/strategy_evaluation.json); None if it hasn't run yet. Read-only, never raises."""
    p = DATA_DIR / "reports" / "strategy_evaluation.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "recommended": d.get("recommended_live_arm"),
        "current": d.get("current_live_arm"),
        "action": d.get("action"),
        "switch": bool(d.get("switch")),
        "reason": d.get("reason"),
        "apply_cmd": d.get("apply_cmd"),
        "auto_apply_live": bool(d.get("auto_apply_live")),
        "hysteresis": d.get("hysteresis") or None,
        "scores": (d.get("scores") or [])[:8],  # top 8 ranked arms for the UI
        "ts": d.get("ts"),
    }


# --------------------------------------------------------------------------- #
# Pushed-snapshot store: "file" (default, single-instance) | "redis" (shared,
# lets N read replicas serve the same pushed snapshot — see SCALABILITY.md §5).
# --------------------------------------------------------------------------- #
_PUSHED_REDIS_KEY = "ictbot:pushed_snapshot"
_redis_client = None  # lazily-created singleton (only when SNAPSHOT_STORE=redis)


def _snapshot_backend() -> str:
    return (settings.snapshot_store or "file").lower()


def _redis():
    """Lazy redis client — only imported/connected when SNAPSHOT_STORE=redis and REDIS_URL is set.
    Returns None on any import/connection error so callers fall back to the file path; the read
    surface never breaks because an optional dependency is missing or a cache is down."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not settings.redis_url:
        return None
    try:
        import redis  # optional dep ([scale] extra); only needed for multi-instance read scaling

        _redis_client = redis.Redis.from_url(
            settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        return _redis_client
    except Exception:
        return None


def store_pushed_snapshot(data: dict) -> None:
    """Persist a pushed live snapshot. Default ("file") writes atomically (tmp + os.replace, like the
    allocator's save_state) so a concurrent /api/snapshot read never sees a half-written file.
    "redis" writes a shared key with a TTL so every read replica serves the same snapshot. If redis
    is selected but unreachable we fall through to the file so a push is never silently lost. Caller
    (ingest.py) has already validated the token + shape."""
    if _snapshot_backend() == "redis":
        client = _redis()
        if client is not None:
            ttl_s = max(1, int(float(settings.pushed_snapshot_ttl_h) * 3600))
            client.set(_PUSHED_REDIS_KEY, json.dumps(data, default=str), ex=ttl_s)
            return
    PUSHED.parent.mkdir(parents=True, exist_ok=True)
    tmp = PUSHED.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    os.replace(tmp, PUSHED)


def pushed_snapshot() -> dict | None:
    """The most recent pushed live snapshot IFF it exists and its `served_at` is within
    `pushed_snapshot_ttl_h` — else None (so /api/snapshot falls back to the baked-seed
    journal read). Best-effort: any parse/clock/redis error → None (never breaks the read path).
    The TTL stops a stale override from sticking forever (e.g. a persisted file after the
    pusher stopped); on a free-tier cold-start the file is simply gone → None. Reads the shared
    redis key when SNAPSHOT_STORE=redis, else the local atomic-write file."""
    try:
        if _snapshot_backend() == "redis":
            client = _redis()
            if client is None:
                return None
            raw = client.get(_PUSHED_REDIS_KEY)
            if not raw:
                return None
            data = json.loads(raw)
        else:
            if not PUSHED.exists():
                return None
            data = json.loads(PUSHED.read_text(encoding="utf-8"))
        ts = data.get("served_at")
        if not ts:
            return None
        served = datetime.fromisoformat(str(ts))
        if served.tzinfo is None:
            served = served.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - served).total_seconds() / 3600.0
        if age_h > float(settings.pushed_snapshot_ttl_h):
            return None
        return data
    except Exception:
        return None


def snapshot() -> dict:
    """One aggregate read for the React poll loop. Each section is independently
    guarded so a single failure degrades that card, not the whole dashboard."""
    rows = read_journal()

    def _safe(fn, *a):
        try:
            return fn(*a)
        except Exception:
            return None

    return {
        "health": _safe(health_card) or {"ok": False},
        "identity": identity_card(),
        "strategy": _safe(strategy_card, rows) or None,
        "strategies": _safe(strategies_card) or {"items": [], "current": ""},
        # The REAL live/contest arm (journal- or STRATEGY_NAME-derived) + its gate — distinct from
        # the SIM Strategy Lab above; surfaces which arm is trading real money.
        "live_arm": _safe(live_arm_card) or None,
        "state": _safe(state_card, rows) or {},
        "nav": _safe(nav_card, rows) or {},
        "regime": _safe(regime_card, rows) or {},
        "rebalances": _safe(rebalances_card, 10, rows) or {"items": []},
        "rationale": _safe(rationale_card, 20, rows) or {"items": []},
        # Per-token rotation: which of the 8 have been touched (momentum-held vs ~0-NAV floor nudge).
        "token_rotation": _safe(token_rotation_card, rows)
        or {"tokens": [], "touched_count": 0, "total": 0, "held": [], "nudged": []},
        "pillars": _safe(pillars_card, rows) or {},
        # LIVE on-chain real funds (separate ledger from the SIM NAV above).
        "wallet": _safe(wallet_card) or {"ok": False},
        # CMC Startup-tier market intelligence + the CMC credit-budget telemetry.
        "market_intel": _safe(market_intel_card, rows) or {"enabled": False},
        "cmc_api": _safe(cmc_api_card) or {},
        # CMC Agent Hub — the Data MCP + Skills Marketplace + x402 exhibit (legacy; off under free data).
        "agent_hub": _safe(agent_hub_card, rows) or {"mcp_enabled": False},
        # Market Data Hub — the live FREE-data-stack exhibit (Binance · alternative.me · DexScreener · CoinGecko).
        "market_data_hub": _safe(market_data_hub_card),
        # Consolidated agent-economy P&L: trading PnL + commerce revenue − x402 spend (one 'true position').
        "economy": _safe(economy_card) or None,
        # Forward-gated strategy auto-selector — the recommended LIVE arm + ranked scores (recommend-only).
        "auto_selector": _safe(evaluation_card) or None,
        # Scheduler health — last live-tick + dd-watch ages, so a silent cron death is caught fast.
        "scheduler": _safe(scheduler_card) or None,
        # Server clock at read time — lets the SPA show how fresh the data is (and
        # detect a frozen static fallback) instead of trusting tx timestamps alone.
        "served_at": datetime.now(timezone.utc).isoformat(),
    }


# In-process micro-cache for the heavy journal-rebuild fallback. /api/snapshot serves the pushed
# override first (a cheap single read); this only kicks in on the fallback, so a burst of cache-miss
# reads (cold edge / stale-while-revalidate refresh / no CDN at all) doesn't re-parse 500 journal
# lines and rebuild ~18 cards on every request. TTL is small enough (default 2s) that a fresh ingest
# is visible within one SPA poll. See SCALABILITY.md §4.
_snap_cache: dict = {"value": None, "ts": 0.0}
_snap_lock = threading.Lock()


def snapshot_cached() -> dict:
    """`snapshot()` behind a short in-process TTL cache (settings.snapshot_cache_ttl_s) with
    single-flight + serve-stale. TTL<=0 disables the cache (always rebuild). Safe per-replica: every
    replica reads the same journal / pushed snapshot, so independent caches converge.

    snapshot() can be slow on a cold cache (per-card network reads), so we (a) stamp the cache time
    AFTER the build (else the entry is born already-expired), and (b) let only ONE thread rebuild
    while a burst of concurrent cache-miss requests serves the last-good value — so N viewers hitting
    the fallback at once trigger one rebuild, not N (which would melt the free-tier instance)."""
    ttl = float(settings.snapshot_cache_ttl_s)
    if ttl <= 0:
        return snapshot()
    now = time.time()
    cached = _snap_cache["value"]
    if cached is not None and now - _snap_cache["ts"] < ttl:
        return cached
    # Stale or cold. Block ONLY when we have nothing to serve; otherwise serve stale and let the
    # one thread that holds the lock refresh in the background.
    got_lock = _snap_lock.acquire(blocking=cached is None)
    if not got_lock:
        return cached  # another thread is already rebuilding — serve the last good value meanwhile
    try:
        # Re-check under the lock: another thread may have just refreshed it while we waited.
        if _snap_cache["value"] is not None and time.time() - _snap_cache["ts"] < ttl:
            return _snap_cache["value"]
        val = snapshot()
        _snap_cache.update(value=val, ts=time.time())  # stamp AFTER the (possibly slow) build
        return val
    except Exception:
        if _snap_cache["value"] is not None:
            return _snap_cache["value"]  # rebuild failed but we have a prior value — never blank
        raise
    finally:
        _snap_lock.release()


def invalidate_snapshot_cache() -> None:
    """Drop the micro-cache so the next /api/snapshot rebuild reflects fresh state immediately
    (called after an ingest push)."""
    _snap_cache.update(value=None, ts=0.0)
