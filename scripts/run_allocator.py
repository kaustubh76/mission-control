#!/usr/bin/env python3
"""
BNB-contest agent runtime: the momentum-allocator rebalance tick.

Each invocation = one rebalance decision:
  1. pull 4h candles for the 8 contest tokens (CMC feed -> Binance fallback)
  2. compute target weights (momentum allocator, cap = the risk dial)
  3. enforce the drawdown halt (NAV vs high-water mark; flatten + stop if breached)
  4. rebalance the book toward target via TWAK spot swaps (sim or live)
  5. journal the tick (NAV, weights, swaps, fees)

Designed to be driven on a schedule (cron / scheduler) every `alloc_rebal_bars`
(default daily). `--loop` runs it in-process for local dry-runs. SIM mode (default)
persists a paper ledger across ticks; LIVE mode reads real on-chain balances and
requires ENABLE_LIVE_TRADING=true + the trust-wallet-cli.

Usage:
  python scripts/run_allocator.py                 # one sim tick
  python scripts/run_allocator.py --loop --interval-min 5 --ticks 3
  python scripts/run_allocator.py --mode live     # real BSC swaps (guarded)
  python scripts/run_allocator.py --reset         # wipe paper ledger / HWM
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ictbot.agent.rationale import explain as explain_decision
from ictbot.data.cmc import fear_greed, fetch_4h, price_fn
from ictbot.engine.portfolio_replay import align_close_matrix
from ictbot.exec.bsc_spot_live import TwakSpotBroker
from ictbot.exec.twak_client import make_client
from ictbot.runtime import active_tokens as _active_tokens
from ictbot.runtime import kill_switch
from ictbot.runtime import strategy_select as _strategy_select
from ictbot.settings import JOURNAL_DIR, settings
from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import (
    CONTEST_TOKENS,
    AllocatorParams,
    cmc_seed_vol_floor,
)
from ictbot.strategy.regime_score import (
    RegimeIntel,
    regime_breakdown,
)


def _cmc_credits_today() -> int | None:
    """Credits the CMC client has spent today (for the journal + dashboard). None on any error."""
    try:
        from ictbot.data.cmc_client import CMC
        return CMC.telemetry()["credits_today"]
    except Exception:
        return None

# Keep the SIM paper track and the LIVE contest track FULLY separate (journal +
# state) so the $1000 paper ledger never leaks into the real live portfolio or the
# NAV curve / dashboard.
SIM_JOURNAL = JOURNAL_DIR / "allocator_journal.jsonl"
LIVE_JOURNAL = JOURNAL_DIR / "allocator_live.jsonl"
SIM_STATE = JOURNAL_DIR / "allocator_state.json"
LIVE_STATE = JOURNAL_DIR / "allocator_live_state.json"
# 'dryrun' = the quote-only integration track: the FULL live loop against the real twak
# CLI (real on-chain balances + router quotes) but execute=False — nothing signed/spent.
# Its journal + state are SEPARATE so a dry-run can never touch the real contest's
# allocator_live.* files. Flip to real execution at contest start by dropping --quote-only.
DRYRUN_JOURNAL = JOURNAL_DIR / "allocator_dryrun.jsonl"
DRYRUN_STATE = JOURNAL_DIR / "allocator_dryrun_state.json"

# Live ticks refuse to trade on candles older than this (dead feed / stale cache).
MAX_BAR_AGE_H = 12.0


def journal_path(mode: str):
    if mode == "dryrun":
        return DRYRUN_JOURNAL
    return LIVE_JOURNAL if mode == "live" else SIM_JOURNAL


def state_path(mode: str):
    if mode == "dryrun":
        return DRYRUN_STATE
    return LIVE_STATE if mode == "live" else SIM_STATE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(mode: str = "sim") -> dict:
    sp = state_path(mode)
    if sp.exists():
        try:
            return json.loads(sp.read_text())
        except Exception:
            pass
    return {"hwm": None, "halted": False, "balances": None,
            "cumulative_swaps": 0, "window_start_ts": None, "floor_cursor": 0}


def save_state(state: dict, mode: str = "sim") -> None:
    # Atomic write: tmp + os.replace so a crash mid-flush never leaves a
    # half-written JSON that the next read would discard (which would silently
    # reset the HWM and defeat the drawdown halt). Mirrors runtime/heartbeat.py.
    sp = state_path(mode)
    tmp = sp.with_suffix(sp.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, sp)


def journal(entry: dict, mode: str = "sim") -> None:
    # default=str: a journal row is the audit trail for a tick that may have ALREADY executed swaps,
    # so an unexpected non-serializable field (e.g. a web3 AttributeDict from an SDK result) must
    # never crash the write and lose the row — coerce stragglers to their string repr instead.
    with journal_path(mode).open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def build_broker(mode: str, pf, state: dict, active: list[str] | None = None):
    client = make_client(
        mode, pf,
        start_usdt=settings.alloc_start_usdt,
        fee_per_side=settings.fee_per_side,
        slippage_per_side=settings.slippage_per_side,
    )
    on_chain = mode in ("live", "dryrun")  # reads real balances (dryrun = live, quote-only)
    # SIM: restore the paper ledger so NAV/holdings persist across ticks.
    if mode == "sim" and state.get("balances"):
        client._bal = dict(state["balances"])
    # UI token toggles: the broker trades over active ∪ still-held — a deselected
    # token with a balance MUST stay in the loop so the next rebalance sells it
    # (target 0) instead of stranding it outside the trading universe. LIVE reads
    # held from ON-CHAIN truth (client.balances()), NOT the previous tick's journal
    # snapshot: a token held on-chain but missing from a stale/partial snapshot
    # would otherwise drop out of broker.nav() — understating NAV vs the persisted
    # HWM and risking a FALSE drawdown halt. Unknown holdings (no state yet, or a
    # failed live read) degrade to the full universe — identical to legacy.
    if active is None:
        universe = CONTEST_TOKENS
    else:
        if on_chain:
            try:
                bal = client.balances()
            except Exception:
                bal = None        # can't see the chain → assume anything may be held
        else:
            bal = state.get("balances")
        held = (set(CONTEST_TOKENS) if bal is None
                else {t for t, q in bal.items() if t in CONTEST_TOKENS and q and q > 0})
        keep = set(active) | held
        universe = tuple(t for t in CONTEST_TOKENS if t in keep)
    broker = TwakSpotBroker(
        client,
        tokens=universe,
        min_rebal_frac=settings.alloc_min_rebal_frac,
        min_swap_usd=settings.alloc_min_swap_usd,
        live=on_chain,
        live_enabled=settings.enable_live_trading,
        dry_run=(mode == "dryrun"),
    )
    return broker, client


def _resolve_strategy_name(mode: str) -> str:
    """Which registered strategy this tick runs.

    SIM honors the dashboard's file-config selector (strategy_select, re-read fresh
    each tick); LIVE IGNORES it and always uses the default — contest-safety: a
    dashboard click can never change the live/contest strategy. STRATEGY_NAME unset +
    ALLOC_ADAPTIVE on => "momentum_adaptive" (the bit-for-bit locked default).
    """
    default_name = settings.strategy_name or (
        "momentum_adaptive" if settings.alloc_adaptive else "momentum"
    )
    return _strategy_select.load(default_name) if mode == "sim" else default_name


def _assert_live_arm_gate(name: str) -> int | None:
    """Pre-arm safety gate for a LIVE run: check the arm's acceptance verdict in
    data/reports/strategy_gates.json. REFUSE (return 2) ONLY on an explicit survival failure (the hard
    DQ rail) — you never arm a DQ-unsafe arm. A forward-not-eligible arm or a missing gate file →
    WARN + proceed (the gate file is gitignored/campaign-only, so a host that simply lacks it must not
    be blocked from trading). Returns None when OK to proceed. Reads the MAIN data tree's reports
    (a real live tick has ALLOCATOR_DATA_DIR unset → repo/data/reports)."""
    gp = JOURNAL_DIR.parent / "reports" / "strategy_gates.json"
    try:
        g = (json.loads(gp.read_text()) or {}).get(name) if gp.exists() else None
    except Exception:
        g = None
    if not g:
        print(f"[{_now()}] live arm {name!r}: no gate verdict on file — proceeding UNVERIFIED "
              f"(populate via `python scripts/arm_check.py --arm {name}` / forward reports).")
        return None
    surv = g.get("survival") or {}
    if surv.get("passed") is False:
        print(f"[{_now()}] REFUSING live arm {name!r}: survival gate FAILED "
              f"(worst-week DD {surv.get('worst_week_dd')}) — DQ-unsafe. Pick a survival-passed arm.")
        return 2
    if not (g.get("forward") or {}).get("forward_eligible"):
        print(f"[{_now()}] WARNING live arm {name!r}: survival ✓ but forward gate NOT eligible "
              f"(accruing) — running on operator sign-off, not forward proof.")
    return None


def params() -> AllocatorParams:
    return AllocatorParams(
        lookback=settings.alloc_lookback,
        top_k=settings.alloc_top_k,
        deploy_cap=settings.alloc_deploy_cap,
        vol_lookback=settings.alloc_vol_lookback,
        rebal_bars=settings.alloc_rebal_bars,
        abs_filter=settings.alloc_abs_filter,   # False (default) = active: always deploy top-k
    )


def _trade_floor_shortfall(cum: int, now: datetime | None = None) -> int:
    """How many more swaps to bank NOW to clear the >=7 floor, else 0.

    Fires ONLY inside [contest_start, contest_end] AND within
    trade_floor_lookahead_days of the end — so pre-contest sim never nudges.
    """
    floor = int(settings.trade_floor_min)
    if cum >= floor:
        return 0
    try:
        now = now or datetime.now(timezone.utc)
        start = datetime.fromisoformat(settings.contest_start).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(settings.contest_end).replace(tzinfo=timezone.utc)
    except Exception:
        return 0
    if not (start <= now <= end):
        return 0
    days_left = (end - now).total_seconds() / 86400.0
    if days_left > float(settings.trade_floor_lookahead_days):
        return 0
    return floor - cum


def _floor_picker(start_cursor: int, tokens: list[str]):
    """Round-robin token picker for the trade-floor nudge — touches every token over the contest
    week instead of always nudging the largest holding. Returns (pick, get_cursor): pick() yields
    the next token and advances; get_cursor() reads the advanced cursor (persist it in state)."""
    box = {"i": int(start_cursor)}

    def pick() -> str:
        tok = tokens[box["i"] % len(tokens)]
        box["i"] += 1
        return tok

    return pick, (lambda: box["i"])


def _ensure_trade_floor(broker, prices: dict, needed: int, *, pick=None):
    """Bank `needed` real swaps via small round-trips (buy a sliver, sell it back)
    to clear the contest's >=7-trade floor. ~0 NAV impact (minus tiny fees).
    Returns (swaps, banked). Stops early if it can't fund a sliver or a leg fails.

    `pick`: optional callable yielding the token to nudge each round-trip (round-robin rotation,
    so the floor touches the whole universe over the week). None (default) = the legacy behaviour:
    nudge the largest USD holding (else tokens[0]) — bit-for-bit unchanged."""
    swaps, banked = [], 0
    default_tok = None
    if pick is None:
        holdings = broker.holdings_usd(prices)
        default_tok = max(holdings, key=holdings.get) if any(v > 0 for v in holdings.values()) else None
        if default_tok is None or default_tok not in broker.tokens:
            default_tok = broker.tokens[0]
    sliver = max(broker.min_swap_usd * 1.5, 2.0)
    _exec = not getattr(broker, "dry_run", False)  # quote-only floor nudge under --quote-only
    while banked < needed:
        tok = pick() if pick is not None else default_tok
        spend = min(sliver, broker.client.balance(broker.quote))
        if spend < broker.min_swap_usd:
            break
        s1 = broker.client.swap(broker.quote, tok, spend, execute=_exec)
        swaps.append(s1)
        if not s1.ok:
            break
        banked += 1
        if s1.amount_to > 0:
            s2 = broker.client.swap(tok, broker.quote, s1.amount_to, execute=_exec)
            swaps.append(s2)
            if not s2.ok:
                break
            banked += 1
    return swaps, banked


def _floor_nudge(broker, prices: dict, needed: int, state: dict):
    """Trade-floor nudge that ROTATES across the universe (`settings.trade_floor_rotate`, on by
    default) so every token is touched over the contest week, else the legacy largest-holding nudge.
    Advances + persists `state['floor_cursor']` by the round-trips actually banked. Returns
    (swaps, banked) — the contest-only floor mechanism; the momentum allocation is untouched."""
    pick = get_cursor = None
    if settings.trade_floor_rotate:
        pick, get_cursor = _floor_picker(state.get("floor_cursor", 0), broker.tokens)
    swaps, banked = _ensure_trade_floor(broker, prices, needed, pick=pick)
    if get_cursor is not None and banked:
        state["floor_cursor"] = get_cursor()
    return swaps, banked


def _nudged_tokens(swaps, quote: str) -> list[str]:
    """Distinct tokens the floor nudge actually bought (each round-trip's buy leg `to_token`), in
    first-seen order. Lets the FLOOR_NUDGE journal row — and the dashboard — show WHICH tokens the
    contest-floor rotation touched, not just how many trades it banked. Failed/quote legs excluded."""
    seen: set[str] = set()
    out: list[str] = []
    for s in swaps:
        tok = getattr(s, "to_token", None)
        if getattr(s, "ok", False) and tok and tok != quote and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _profit_lock_eval(state: dict, nav: float, *, trigger: float, trail: float,
                      min_keep: float, bank: float) -> tuple[str, dict]:
    """PURE profit-lock ratchet decision (no I/O, no broker) — the caller
    flattens/journals/persists. Returns (action, state_updates):

      action ∈ {"none", "arm", "bank", "trail"}

    Semantics vs the campaign anchor `campaign_start_nav`:
      cum >= bank          -> "bank"  (flatten: campaign target reached outright)
      armed & nav < floor  -> "trail" (flatten: gave back `trail` from the peak,
                                       floor never below anchor*(1+min_keep))
      cum >= trigger       -> "arm"   (start trailing; seed the peak)
    The peak only ratchets UP; missing/invalid anchor or NAV is a no-op (the
    ratchet must never act on bad data — same contract as the dd guards)."""
    anchor = state.get("campaign_start_nav")
    if not (isinstance(anchor, (int, float)) and anchor > 0 and nav and nav > 0):
        return "none", {}
    cum = nav / float(anchor) - 1.0
    if cum >= bank:
        return "bank", {"peak_since_trigger": max(nav, float(state.get("peak_since_trigger") or 0.0))}
    if state.get("profit_lock_armed"):
        peak = max(nav, float(state.get("peak_since_trigger") or nav))
        lock_floor = max(float(anchor) * (1.0 + min_keep), peak * (1.0 - trail))
        if nav < lock_floor:
            return "trail", {"peak_since_trigger": peak, "lock_floor": lock_floor}
        return "none", {"peak_since_trigger": peak, "lock_floor": lock_floor}
    if cum >= trigger:
        lock_floor = max(float(anchor) * (1.0 + min_keep), nav * (1.0 - trail))
        return "arm", {"profit_lock_armed": True, "peak_since_trigger": nav,
                       "lock_floor": lock_floor}
    return "none", {}


def _swaps_today(mode: str, now: datetime | None = None) -> int:
    """Successful swaps banked TODAY (UTC date) per the journal — the source of
    truth for the >=1-trade/day floor (no state-schema change): REBALANCE rows
    contribute `n_swaps`, FLOOR_NUDGE rows contribute `banked`."""
    now = now or datetime.now(timezone.utc)
    day = now.date().isoformat()
    jp = journal_path(mode)
    if not jp.exists():
        return 0
    total = 0
    try:
        with jp.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not str(row.get("ts", "")).startswith(day):
                    continue
                ev = row.get("event")
                if ev == "REBALANCE":
                    total += int(row.get("n_swaps") or 0)
                elif ev == "FLOOR_NUDGE":
                    total += int(row.get("banked") or 0)
    except Exception:
        return 0
    return total


def _last_halt_partial(mode: str) -> dict | None:
    """If the MOST RECENT DD_HALT/PROFIT_LOCK journal row recorded a PARTIAL flatten (a sell leg failed
    after retries → possible residual on-chain exposure), return a summary; else None. `--resume` uses
    it to refuse clearing a halt that may have left the book non-flat without an explicit `--force`."""
    jp = journal_path(mode)
    if not jp.exists():
        return None
    last = None
    try:
        with jp.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("event") in ("DD_HALT", "PROFIT_LOCK"):
                    last = row
    except Exception:
        return None
    if last and last.get("flatten_partial"):
        return {"flattened_ok": last.get("flattened_ok"),
                "attempted": last.get("flattened_attempted"), "errors": last.get("flatten_errors")}
    return None


def _bar_age_hours(mat) -> float | None:
    """Age of the most recent aligned candle, in hours (None if undeterminable)."""
    try:
        ts = pd.Timestamp(mat.index[-1])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return (datetime.now(timezone.utc) - ts.to_pydatetime()).total_seconds() / 3600.0
    except Exception:
        return None


def _live_preflight(dry_run: bool = False) -> int | None:
    """Fail-fast checks before a LIVE tick: kill switch, creds, wallet password, enable flag.
    Returns a skip code (2) with a clear reason, or None when good to proceed.

    Shared by _tick / _dd_watch / _daily_floor / --preflight-only, so the kill switch halts EVERY
    live entry point — including a long-running `--loop` process (settings.enable_live_trading is read
    once at import; the sentinel file is the only in-process halt, mirroring scanner.py).

    `dry_run` (the --quote-only integration track): twak `price` / `swap --quote-only` need no
    creds, no wallet password and no ENABLE_LIVE_TRADING — nothing is signed or spent — so only the
    kill switch is enforced. This keeps the dry-run runnable during integration before creds/funding
    are wired, while the real LIVE path still demands all four."""
    if kill_switch.is_engaged():
        print(f"[{_now()}] LIVE preflight FAIL: KILL SWITCH ENGAGED — refusing to trade.")
        return 2
    if dry_run:
        return None
    if not (settings.twak_access_id and settings.twak_hmac_secret):
        print(f"[{_now()}] LIVE preflight FAIL: TWAK_ACCESS_ID / TWAK_HMAC_SECRET missing.")
        return 2
    if not (settings.twak_wallet_password or settings.agent_wallet_password):
        print(f"[{_now()}] LIVE preflight FAIL: wallet password missing "
              f"(TWAK_WALLET_PASSWORD / AGENT_WALLET_PASSWORD).")
        return 2
    if not settings.enable_live_trading:
        print(f"[{_now()}] LIVE preflight FAIL: ENABLE_LIVE_TRADING is false.")
        return 2
    return None


def _reconcile_live(client, expected: dict | None, tol: float = 0.02) -> dict | None:
    """Compare on-chain balances to the journal-expected ones. Returns a drift report
    {token: {expected, actual}} for any token off by > `tol` (fraction), else None.
    Non-fatal — surfaced as a RECON_DRIFT journal event so divergence (MEV / partial
    fill / external transfer) is visible rather than silently compounding."""
    if not expected:
        return None
    try:
        actual = client.balances()
    except Exception:
        return None
    drift = {}
    for tok, exp in expected.items():
        act = float(actual.get(tok, 0.0))
        if exp and abs(act - float(exp)) / abs(float(exp)) > tol:
            drift[tok] = {"expected": round(float(exp), 8), "actual": round(act, 8)}
    return drift or None


def _net_external_flow(drift: dict | None, prices: dict) -> float:
    """Net USD value of a reconcile drift: positive = external DEPOSIT (operator gas
    top-up / inbound transfer), negative = WITHDRAWAL. Each token's (actual − expected)
    qty is priced at `prices` (USDT = $1; a token with no price contributes 0). Feeds the
    deposit-aware re-baselining guard so an external flow counts as CAPITAL, not PnL."""
    if not drift:
        return 0.0
    net = 0.0
    for tok, d in drift.items():
        px = 1.0 if tok == "USDT" else float(prices.get(tok) or 0.0)
        net += (float(d.get("actual", 0.0)) - float(d.get("expected", 0.0))) * px
    return net


def _apply_capital_flow(state: dict, drift: dict | None, prices: dict, nav: float,
                        client, mode: str) -> bool:
    """Deposit-aware re-baselining. If `drift` is a MATERIAL external flow (|net USD| >=
    max($0.25, 1% of NAV)), treat it as CAPITAL — shift the campaign anchor + HWM by the
    net flow so `PnL = NAV - anchor` stays TRADING-ONLY (a gas top-up is not profit) and a
    deposit-inflated NAV can't misfire the profit-lock / drawdown halt. Refreshes balances,
    persists, and journals a CAPITAL_FLOW row. Returns True when it handled a flow — the
    caller then SKIPS this tick's trade/profit-lock/DD (re-baseline only, never trade on a
    deposit tick). Gated by ALLOC_DEPOSIT_AWARE. NOTE: a large MEV / partial-fill drift
    could be misread as a deposit; the materiality floor + the no-trade rule bound the
    downside on this small book."""
    if not (settings.alloc_deposit_aware and drift):
        return False
    net_flow = _net_external_flow(drift, prices)
    if abs(net_flow) < max(0.25, 0.01 * nav):
        return False
    anchor0 = state.get("campaign_start_nav")
    if isinstance(anchor0, (int, float)) and anchor0 > 0:
        state["campaign_start_nav"] = round(float(anchor0) + net_flow, 6)
    if state.get("hwm"):
        state["hwm"] = round(float(state["hwm"]) + net_flow, 6)
    try:
        state["balances"] = client.balances()
    except Exception:
        pass
    save_state(state, mode)
    kind = "deposit" if net_flow > 0 else "withdrawal"
    journal({"ts": _now(), "event": "CAPITAL_FLOW", "mode": mode,
             "net_flow_usd": round(net_flow, 4), "kind": kind,
             "new_anchor": state.get("campaign_start_nav"),
             "new_hwm": state.get("hwm")}, mode)
    print(f"[{_now()}] CAPITAL_FLOW {net_flow:+.4f} USD (external {kind}); re-anchored to "
          f"{state.get('campaign_start_nav')}, HWM {state.get('hwm')}. "
          f"Re-baseline only — not trading this tick.")
    return True


def _acquire_lock(mode: str):
    """Non-blocking per-mode exclusive lock (returns the fd, or None if held).

    Prevents two ticks running at once (cron overlap + a manual run, a slow tick
    still in flight when the next cron fires) — which on LIVE would double-execute
    the same rebalance. Sim and live use separate locks so a dry-run never blocks
    the contest tick.
    """
    fd = os.open(str(JOURNAL_DIR / f".allocator_{mode}.lock"), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def _acquire_redis_lease(mode: str) -> str | None:
    """Cross-host singleton backstop (ALLOCATOR_LOCK=redis). Acquire a shared single-key lease via
    `SET allocator_lock:<mode> <token> NX PX <ttl>` so the allocator can never run active-active on
    two machines even if accidentally double-launched. Returns the token on success, None if the
    lease is held elsewhere OR redis is unreachable while ENABLED (fail-CLOSED: a write tick must not
    proceed when its only cross-host guard is down). When ALLOCATOR_LOCK!=redis it's a no-op that
    returns a sentinel so the caller treats it as "acquired". TTL is a ceiling well above a real tick
    (a crashed holder self-heals); ticks are far shorter, so no mid-tick renewal is needed."""
    if settings.allocator_lock != "redis":
        return "flock-only"  # sentinel: redis lease not in use → treat as acquired
    if not settings.redis_url:
        print(f"[{_now()}] SKIP: ALLOCATOR_LOCK=redis but REDIS_URL is unset (refusing to tick).")
        return None
    try:
        import uuid

        import redis  # optional dep ([scale] extra); only needed for cross-host locking

        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        token = uuid.uuid4().hex
        ttl_ms = max(1000, int(settings.allocator_lock_ttl_s) * 1000)
        if client.set(f"allocator_lock:{mode}", token, nx=True, px=ttl_ms):
            return token
        print(f"[{_now()}] SKIP: an allocator {mode} lease is held on another host.")
        return None
    except Exception as e:  # fail-closed: never run the write tick without its cross-host guard
        print(f"[{_now()}] SKIP: redis lease unavailable ({str(e)[:80]}); refusing to tick.")
        return None


def _release_redis_lease(mode: str, token: str | None) -> None:
    """Release the lease ONLY if we still hold it (value matches our token) — never free a lease that
    already expired and was re-acquired by another host. No-op for the flock-only sentinel."""
    if not token or token == "flock-only" or settings.allocator_lock != "redis":
        return
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        # Compare-and-delete so we don't release someone else's re-acquired lease.
        lua = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        client.eval(lua, 1, f"allocator_lock:{mode}", token)
    except Exception:
        pass  # lease TTL will reclaim it; release is best-effort


def tick(mode: str, dd_cap: float) -> int:
    """Idempotency wrapper: hold the per-mode lock for the whole rebalance. The local flock guards
    two ticks on ONE host; the optional redis lease (ALLOCATOR_LOCK=redis) additionally guards
    cross-host double-launch. The allocator is single-writer by design and is NEVER load-balanced."""
    fd = _acquire_lock(mode)
    if fd is None:
        print(f"[{_now()}] SKIP: an allocator {mode} tick is already running (lock held).")
        return 2
    lease = _acquire_redis_lease(mode)
    if lease is None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        return 2
    try:
        return _tick(mode, dd_cap)
    finally:
        _release_redis_lease(mode, lease)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _flatten_fields(flat: list) -> dict:
    """Journal fields for an emergency_flatten result. Distinguishes CONFIRMED sells from ATTEMPTS so
    a PARTIAL flatten (a leg that failed after retries → residual on-chain exposure) is never masked as
    'all flat'. `flattened` is kept (== attempted) for back-compat; `flatten_partial`/`flatten_errors`
    surface the failure the bare count hid. Mirrors the FLOOR_NUDGE_FAILED row's n_attempted+errors."""
    failed = [s for s in flat if not getattr(s, "ok", False)]
    return {"flattened": len(flat),
            "flattened_ok": len(flat) - len(failed), "flattened_attempted": len(flat),
            "flatten_partial": bool(failed),
            "flatten_errors": [getattr(s, "error", None) for s in failed][:5]}


def _tick(mode: str, dd_cap: float) -> int:
    dry_run = mode == "dryrun"
    if mode in ("live", "dryrun"):
        rc = _live_preflight(dry_run=dry_run)
        if rc is not None:
            return rc
    state = load_state(mode)
    pf = price_fn(settings.cmc_api_key or None)
    # UI token toggles — re-read FRESH each tick (the dashboard may have changed it
    # since the last run; a mid-tick change simply applies next tick). Candles + the
    # regime/breadth matrix stay FULL-universe: the `shape[1] < 3` guard below and the
    # market-gauge semantics both depend on that.
    active = _active_tokens.load()

    # 1. candles -> target weights. The candle SOURCE is per-arm. The DEFAULT is now "cmc_4h": the
    # ENTIRE system is CoinMarketCap-sourced — every arm (momentum, the locked momentum_adaptive, the
    # challengers) decides on CMC's own candles, ZERO CEX. The "binance_4h" else-branch is guarded dead
    # code — only reachable if an arm explicitly sets candle_source="binance_4h" AND CMC_ONLY is unset
    # (the opt-in dev reference). The arms' ALGORITHM is unchanged; only the matrix provenance flips.
    strat_name = _resolve_strategy_name(mode)
    # LIVE pre-arm gate: never trade a survival-FAILED (DQ-unsafe) arm; warn on forward-not-eligible.
    if mode == "live":
        rc = _assert_live_arm_gate(strat_name)
        if rc is not None:
            return rc
    strat = registry.get(strat_name)
    candle_source = getattr(strat, "candle_source", "cmc_4h")
    if candle_source == "cmc_4h":
        from ictbot.data.cmc import cmc_4h_close_matrix

        mat = cmc_4h_close_matrix(CONTEST_TOKENS)
    elif candle_source == "cmc_daily":
        from ictbot.data.cmc import daily_close_matrix

        mat = daily_close_matrix(CONTEST_TOKENS, days=730)
    else:  # "binance_4h" — guarded dev reference only (raises under the CMC_ONLY firewall)
        frames = {t: fetch_4h(t, 2500) for t in CONTEST_TOKENS}
        mat = align_close_matrix(frames, CONTEST_TOKENS)
    if mat.shape[0] < 200 or mat.shape[1] < 3:
        print(f"[{_now()}] insufficient data ({mat.shape}); skipping tick")
        return 2
    # data freshness — never trade on dead candles. Hard-skip in LIVE; warn in sim
    # (forward/paper may legitimately replay a cached snapshot). A DAILY bar is legitimately up to
    # ~24h old, so the cmc_daily arm uses a relaxed staleness bound vs the 4h MAX_BAR_AGE_H.
    age_h = _bar_age_hours(mat)
    max_bar_age = 30.0 if candle_source == "cmc_daily" else MAX_BAR_AGE_H
    if age_h is not None and age_h > max_bar_age:
        msg = f"stale candles ({age_h:.1f}h > {max_bar_age:.0f}h)"
        if mode == "live":
            print(f"[{_now()}] {msg}; skipping LIVE tick")
            return 2
        print(f"[{_now()}] WARNING {msg} (sim continues)")
    p = params()
    # Cold-start seed protection for EVERY cmc_4h arm: the CMC-daily seed is flat intrabar (5-of-6
    # 4h returns = 0) → inverse-vol 1/vol blows up. Inject the shared daily-derived floor so any arm
    # (momentum, momentum_adaptive, the challengers, momentum_cmc) sizes correctly on the seed; it
    # relaxes to a no-op as real streamed 4h bars accrue. Only on cmc_4h — never cmc_daily/binance.
    if candle_source == "cmc_4h":
        from dataclasses import replace as _replace

        _vf = cmc_seed_vol_floor(mat)
        if _vf > 0:
            p = _replace(p, vol_floor=_vf)
    fg = fear_greed(settings.cmc_api_key or None)
    if fg is None:
        print(f"[{_now()}] CMC Fear&Greed unavailable — regime score degrades to breadth+trend.")
    # Enhanced regime (CMC Startup tier) — LIVE-only, A/B-gated. Both flags default OFF,
    # so the validated contest path is bit-for-bit unchanged until promoted after a SIM A/B.
    intel, intel_dict = None, None
    if settings.cmc_regime_enhanced and settings.cmc_intel_enabled:
        try:
            from ictbot.data.cmc_intel import build_regime_intel
            intel_dict = build_regime_intel()
            if intel_dict:
                intel = RegimeIntel(
                    btc_dominance=intel_dict["btc_dominance"],
                    btc_dominance_prev=intel_dict["btc_dominance_prev"],
                    total_mktcap=intel_dict["total_mktcap"],
                    total_mktcap_prev=intel_dict["total_mktcap_prev"],
                    fng_now=intel_dict["fng_now"], fng_7d_avg=intel_dict["fng_7d_avg"],
                    w_dominance=settings.alloc_regime_w_dominance,
                    w_mktcap=settings.alloc_regime_w_mktcap,
                    w_fng_mom=settings.alloc_regime_w_fng_mom,
                )
        except Exception:
            intel, intel_dict = None, None
    # CMC TECHNICAL-ANALYSIS trend-health (the A/B-proven lever) — folded into the deploy
    # cap. Read CMC's AUTHORITATIVE pre-computed daily RSI/MACD/EMA via the Agent Hub MCP;
    # fall back to the local technicals compute. A/B-gated (ALLOC_TA_ENABLED, default OFF).
    ta_health, ta_source, skill = None, None, None
    if settings.alloc_ta_enabled:
        try:
            from ictbot.data import cmc_agent_hub
            ta_health = cmc_agent_hub.basket_ta_health()          # CMC-authoritative
            ta_source = "cmc" if ta_health is not None else None
            if ta_health is None:                                 # local fallback from candles
                from ictbot.strategy import technicals as _tech
                hh = _tech.trend_health(_tech.resample_daily(mat))
                if len(hh) and np.isfinite(hh[-1]):
                    ta_health, ta_source = float(hh[-1]), "local"
        except Exception:
            ta_health, ta_source = None, None
    # CMC composed market-overview SKILL (built on the Data MCP) — agent-ready regime read.
    # Journaled + shown on the dashboard; when CMC_SKILL_REGIME is on it also blends its
    # risk budget into the cap's TA term (LIVE-only, forward-validated — not backtested).
    if settings.cmc_skill_regime:
        try:
            from ictbot.data import cmc_agent_hub
            skill = cmc_agent_hub.market_overview()
            rb = (skill or {}).get("risk_budget")
            if rb is not None:
                ta_health = float(rb) if ta_health is None else 0.5 * (ta_health + float(rb))
                ta_source = (ta_source + "+skill") if ta_source else "skill"
        except Exception:
            skill = None
    # CMC TA-confirmed RANKING (the A/B-proven ta_rank lever) — per-token confirmation tilts
    # the momentum order. CMC-authoritative per-token TA, local fallback. A/B-gated
    # (ALLOC_TA_ENABLED + ALLOC_TA_W_RANK>0). Empty -> the ranking stays the validated baseline.
    ta_token_scores = None
    if settings.alloc_ta_enabled and settings.alloc_ta_w_rank > 0:
        try:
            from ictbot.data import cmc_agent_hub
            ta_token_scores = cmc_agent_hub.token_ta_scores() or None
            if not ta_token_scores:                               # local fallback from candles
                from ictbot.strategy import technicals as _tech
                sc = _tech.token_ta_score(_tech.resample_daily(mat))
                if len(sc):
                    ta_token_scores = {c: float(sc[-1, j]) for j, c in enumerate(mat.columns)
                                       if np.isfinite(sc[-1, j])} or None
        except Exception:
            ta_token_scores = None
    # Regime-adaptive deployment: the cap scales with the LIVE risk-on score (breadth +
    # trend + vol + F&G [+ CMC macro when enhanced] [+ CMC TA when alloc_ta]), so the agent
    # reacts to the unfolding week rather than a frozen backtest cap. ALLOC_ADAPTIVE=false reverts.
    # Dispatch through the strategy registry. STRATEGY_NAME unset (default) derives
    # the locked behavior from ALLOC_ADAPTIVE, so this is bit-for-bit the prior
    # if/else: "momentum_adaptive" (adaptive on) calls adaptive_target_weights with
    # the identical args; "momentum" (adaptive off) calls target_weights_now. New
    # long-only-spot strategies opt in via STRATEGY_NAME (SIM track, gate-promoted).
    # (strat / strat_name already resolved above to pick the candle source.)
    ctx = registry.StratContext(
        params=p, active=active, deploy_cap=settings.alloc_deploy_cap,
        floor=settings.alloc_cap_floor, ceiling=settings.alloc_cap_ceiling,
        ma_window=settings.alloc_breadth_ma, fear_greed=fg, intel=intel,
        ta_health=ta_health, w_ta=settings.alloc_ta_w_cap,
        ta_token_scores=ta_token_scores, w_ta_rank=settings.alloc_ta_w_rank,
    )
    decision = strat.target_weights_now(mat, ctx=ctx)
    weights, score, cap = decision.weights, decision.score, decision.cap
    target = {k: v for k, v in weights.items() if v > 0}
    # CMC universe tilt — re-weight WITHIN the held set by 7d relative strength (SAME
    # deploy cap, SAME cash). A/B-gated (ALLOC_UNIVERSE_TILT, default off). The 7d changes come
    # from the live CMC-WS quote snapshot when fresh (0 credits) else REST quotes/latest —
    # `quote_source` journals which served this tick.
    quote_source = None
    if settings.alloc_universe_tilt and settings.cmc_intel_enabled and len(target) > 1:
        try:
            from ictbot.data import cmc_intel as _ci
            from ictbot.strategy.universe_overlay import momentum_tilt
            target = momentum_tilt(target, _ci.token_changes(list(target.keys())))
            quote_source = _ci.LAST_QUOTE_SOURCE
        except Exception:
            pass

    # CMC on-chain signal overlays — live-only, clamped, gated. Liquidity floor + buy/sell flow tilt
    # + holder-concentration penalty re-weight WITHIN the held set (same total); the liquidity/whale
    # cap brake only LOWERS deployment. PLUS two CMC-native rotation levers — cmc_momentum_tilt
    # (multi-window pct_24h/7d/30d, the richer sibling of the pct_7d momentum_tilt) and sector_tilt
    # (rotate toward CMC's live trending_crypto_narratives). All are STRATEGY-AGNOSTIC (apply to every
    # strategy) and a no-op at their default param → byte-identical to the validated model. Momentum
    # needs only the CEX quote snapshot (independent of the on-chain feed); sector needs only the MCP
    # trending list. Journaled via `onchain_signals`/`cmc_rotation`/`deploy_cap`.
    signal_brake = 1.0
    cmc_rotation = None
    _onchain_ov = settings.cmc_onchain_enabled and (
        settings.alloc_flow_w or settings.alloc_min_vol_usd
        or settings.alloc_max_top10_pct or settings.alloc_liq_brake
    )
    _mom_ov = settings.alloc_mom_multi_w > 0
    _sector_ov = settings.alloc_sector_tilt > 0
    if target and (_onchain_ov or _mom_ov or _sector_ov):
        try:
            from ictbot.strategy import market_signals
            from ictbot.strategy import universe_overlay as _ov
            # token_signals serves the on-chain overlays AND the CMC-native momentum blend (pct_*),
            # so fetch it once when either needs it; sector tilt needs only the trending list.
            _sigs = (market_signals.token_signals(list(target.keys()))
                     if (_onchain_ov or _mom_ov) else {})
            if _onchain_ov:
                target = _ov.liquidity_floor(target, _sigs, settings.alloc_min_vol_usd)
                target = _ov.flow_tilt(target, _sigs, w=settings.alloc_flow_w)
                target = _ov.concentration_penalty(target, _sigs, settings.alloc_max_top10_pct)
                signal_brake = _ov.liquidity_cap_brake(target, _sigs, liq_brake=settings.alloc_liq_brake)
                if signal_brake < 1.0:
                    target = {k: v * signal_brake for k, v in target.items()}
                    cap = cap * signal_brake if cap is not None else cap
            if _mom_ov:
                target = _ov.cmc_momentum_tilt(target, _sigs, w=settings.alloc_mom_multi_w)
                _moms = {s: market_signals.mom_blend(_sigs.get(s) or {}) for s in target}
                cmc_rotation = {"mom": {s: round(v, 4) for s, v in _moms.items() if v is not None}}
            if _sector_ov:
                from ictbot.data import cmc_agent_hub, cmc_sectors
                trending = list((skill or {}).get("narratives") or cmc_agent_hub.trending_narratives())
                target = _ov.sector_tilt(target, trending, cmc_sectors.TOKEN_SECTORS,
                                         w=settings.alloc_sector_tilt)
                cmc_rotation = {**(cmc_rotation or {}), "trending": trending,
                                "sector_hits": sorted(s for s in target
                                                      if cmc_sectors.trending_hits(s, trending))}
        except Exception:
            pass

    broker, client = build_broker(mode, pf, state, active=active)
    # Live balance reconciliation — surface any drift between the journal's expected
    # balances and what's actually on-chain (MEV / partial fill / external transfer).
    drift = None
    if mode in ("live", "dryrun"):
        drift = _reconcile_live(client, state.get("balances"))
        if drift:
            journal({"ts": _now(), "event": "RECON_DRIFT", "mode": mode, "drift": drift}, mode)
            print(f"[{_now()}] RECON_DRIFT: on-chain balances differ from journal: {drift}")
    # A price read can RAISE (cmc.price -> RuntimeError when CMC + Binance both miss).
    # Guard it so the tick exits cleanly via the same skip path as a bad price, rather
    # than aborting with a traceback BEFORE the invalid-price guard below can run.
    try:
        prices = broker.prices()
    except RuntimeError as e:
        print(f"[{_now()}] price read failed ({e}); skipping tick (guards a false DD halt)")
        return 2
    # Price/NAV validity — a transient zero/None price would understate NAV and
    # could trigger a FALSE drawdown halt (liquidation). Skip the tick instead.
    bad_px = [t for t, px in prices.items() if not (isinstance(px, (int, float)) and px > 0)]
    if bad_px:
        print(f"[{_now()}] invalid price(s) {bad_px}; skipping tick (guards a false DD halt)")
        return 2
    nav = broker.nav(prices)
    if not (nav and nav > 0):
        print(f"[{_now()}] NAV={nav} invalid; skipping tick")
        return 2

    # 1b. deposit-aware re-baselining — an external transfer (operator gas top-up) would
    # otherwise inflate NAV (counted as PnL) and could misfire the profit-lock/DD halt
    # (a deposit-inflated NAV crossing the bank threshold has flattened the book before).
    # _apply_capital_flow shifts the anchor + HWM by the flow and signals "skip this tick".
    if mode in ("live", "dryrun") and _apply_capital_flow(state, drift, prices, nav, client, mode):
        return 0

    # 2. drawdown halt (high-water mark)
    hwm = max(nav, state.get("hwm") or nav)
    dd = (hwm - nav) / hwm if hwm > 0 else 0.0
    if state.get("halted"):
        print(f"[{_now()}] HALTED (prior DD breach). NAV={nav:.2f}. Not trading.")
        return 0
    if state.get("profit_locked"):
        # Campaign banked (good path): the book stays flat in USDT. Inside the
        # contest window the trade floors (>=7/week via 3b's shortfall check)
        # are STILL honored with ~0-impact round-trip nudges from the USDT
        # book — a locked campaign must never DQ on the trade count.
        cum = int(state.get("cumulative_swaps", 0))
        need = _trade_floor_shortfall(cum)
        msg = f"[{_now()}] PROFIT-LOCKED (campaign banked). NAV={nav:.2f}. Not rebalancing."
        if need > 0:
            nudge_swaps, banked = _floor_nudge(broker, prices, need, state)
            cum += banked
            if banked:
                state.update(balances=client.balances(), cumulative_swaps=cum)
                save_state(state, mode)
                journal({"ts": _now(), "event": "FLOOR_NUDGE", "mode": mode,
                         "banked": banked, "cumulative_swaps": cum,
                         "tokens": _nudged_tokens(nudge_swaps, broker.quote),
                         "tx": [s.tx for s in nudge_swaps if s.ok]}, mode)
                msg += f" FLOOR_NUDGE banked {banked} -> cum={cum}."
            else:
                journal({"ts": _now(), "event": "FLOOR_NUDGE_FAILED", "mode": mode,
                         "cumulative_swaps": cum, "need": need,
                         "trade_floor_min": settings.trade_floor_min,
                         "n_attempted": len(nudge_swaps),
                         "errors": [s.error for s in nudge_swaps if not s.ok][:3]}, mode)
        print(msg)
        return 0
    if dd > dd_cap:
        flat = broker.emergency_flatten(prices)
        state.update(halted=True, hwm=hwm, balances=client.balances())
        save_state(state, mode)
        journal({"ts": _now(), "event": "DD_HALT", "mode": mode, "source": "daily_tick",
                 "nav": nav, "hwm": hwm, "dd": dd, "dd_cap": dd_cap,
                 **_flatten_fields(flat)}, mode)
        print(f"[{_now()}] DRAWDOWN HALT: dd={dd:.1%} > cap={dd_cap:.1%}. "
              f"Flattened {len(flat)} position(s). NAV={nav:.2f}.")
        return 1

    # 2b. profit-lock ratchet (campaign mode) — lock the GOOD path. Evaluated
    # only on a tick that already survived the drawdown check (the DD halt
    # always wins ties). Default OFF (PROFIT_LOCK_ENABLED) — the validated
    # baseline path is bit-for-bit unchanged when disabled.
    if settings.profit_lock_enabled:
        if not state.get("campaign_start_nav"):
            # Self-init fallback: anchor at the current NAV with an audit row
            # (never crash, never guess silently). Normal path: --anchor-nav.
            state["campaign_start_nav"] = nav
            save_state(state, mode)
            journal({"ts": _now(), "event": "CAMPAIGN_ANCHOR", "mode": mode,
                     "campaign_start_nav": round(nav, 2), "source": "self_init"}, mode)
        pl_action, pl_upd = _profit_lock_eval(
            state, nav,
            trigger=settings.profit_lock_trigger, trail=settings.profit_lock_trail,
            min_keep=settings.profit_lock_min_keep, bank=settings.profit_lock_bank)
        cum_ret = nav / float(state["campaign_start_nav"]) - 1.0
        if pl_action in ("bank", "trail"):
            flat = broker.emergency_flatten(prices)
            state.update(pl_upd)
            state.update(profit_locked=True, hwm=max(hwm, nav), balances=client.balances())
            save_state(state, mode)
            journal({"ts": _now(), "event": "PROFIT_LOCK", "mode": mode,
                     "source": "daily_tick", "kind": pl_action, "nav": round(nav, 2),
                     "campaign_start_nav": round(float(state["campaign_start_nav"]), 2),
                     "cum_ret": round(cum_ret, 4),
                     "peak_since_trigger": round(float(state.get("peak_since_trigger") or nav), 2),
                     "lock_floor": round(float(pl_upd.get("lock_floor") or 0.0), 2) or None,
                     **_flatten_fields(flat)}, mode)
            print(f"[{_now()}] PROFIT LOCK ({pl_action}): cum={cum_ret:+.1%} "
                  f"NAV={nav:.2f}. Flattened {len(flat)} position(s); campaign banked.")
            return 1
        if pl_action == "arm":
            state.update(pl_upd)
            save_state(state, mode)
            journal({"ts": _now(), "event": "PROFIT_LOCK_ARMED", "mode": mode,
                     "source": "daily_tick", "nav": round(nav, 2),
                     "campaign_start_nav": round(float(state["campaign_start_nav"]), 2),
                     "cum_ret": round(cum_ret, 4),
                     "lock_floor": round(float(pl_upd["lock_floor"]), 2)}, mode)
            print(f"[{_now()}] PROFIT LOCK ARMED: cum={cum_ret:+.1%} "
                  f"trailing floor={pl_upd['lock_floor']:.2f}.")
        elif pl_upd:
            state.update(pl_upd)          # peak/floor ratchet bookkeeping
            save_state(state, mode)

    # 3. rebalance
    rep = broker.rebalance(target, prices)
    cum = int(state.get("cumulative_swaps", 0)) + rep.n_swaps

    # 3b. contest trade-floor AUTO-ENSURE — guarantee >=7 trades by banking bounded
    # round-trip nudges if we're behind pace near the deadline (~0 NAV impact).
    nudge_swaps, banked = [], 0
    need = _trade_floor_shortfall(cum)
    if need > 0:
        nudge_swaps, banked = _floor_nudge(broker, prices, need, state)
        cum += banked
        if banked == 0:
            print(f"[{_now()}] WARNING: behind the >=7 trade floor (cum={cum}) but could not "
                  f"bank a nudge (insufficient {broker.quote}).")
            # D2: leave a journal trail when the floor nudge can't bank — otherwise the
            # only evidence of a missed contest-floor top-up is a stdout WARNING.
            journal({"ts": _now(), "event": "FLOOR_NUDGE_FAILED", "mode": mode,
                     "cumulative_swaps": cum, "need": need, "trade_floor_min": settings.trade_floor_min,
                     "n_attempted": len(nudge_swaps),
                     "errors": [s.error for s in nudge_swaps if not s.ok][:3]}, mode)

    # Per-term regime breakdown for the journal + dashboard (pure read; no decision impact).
    regime_terms = None
    if settings.alloc_adaptive:
        try:
            regime_terms = regime_breakdown(mat, ma_window=settings.alloc_breadth_ma,
                                            fear_greed=fg, intel=intel)
        except Exception:
            regime_terms = None
    # The agent's natural-language rationale for THIS decision (reads -> decides -> acts).
    rationale = explain_decision(fear_greed=fg, regime_score=score or 0.0,
                                 deploy_cap=cap, weights=target, intel=intel_dict)
    if settings.profit_lock_enabled and state.get("profit_lock_armed"):
        _plr = nav / float(state["campaign_start_nav"]) - 1.0
        rationale += (f" Profit lock armed at {_plr:+.1%}; trailing floor "
                      f"${float(state.get('lock_floor') or 0.0):,.2f} protects the campaign gain.")
    if not state.get("window_start_ts"):
        state["window_start_ts"] = _now()
    state.update(hwm=max(hwm, rep.nav_after), halted=False,
                 balances=client.balances(), cumulative_swaps=cum)
    save_state(state, mode)
    if banked:
        journal({"ts": _now(), "event": "FLOOR_NUDGE", "mode": mode, "banked": banked,
                 "cumulative_swaps": cum,
                 "tokens": _nudged_tokens(nudge_swaps, broker.quote),
                 "tx": [s.tx for s in nudge_swaps if s.ok]}, mode)
        print(f"[{_now()}] FLOOR_NUDGE: banked {banked} trade(s) -> cum={cum} (>=7 floor).")
    # Pillar 1 — CMC AI Agent Hub x402 paid-data read (real on-chain USDC micropayment
    # via the SDK X402Signer; emits a receipt to data/x402/receipts.json). Enriches the
    # journal with live DEX data for the top target; never drives the trade. Off by default.
    # Track attempted/failed separately so the journal can tell DISABLED vs FAILED vs
    # NO-DATA apart (C1) — otherwise a silently-exhausted Base-USDC pay wallet looks
    # identical to x402 simply being off.
    x402_dex = None
    # Quote-only dry-runs never spend real USDC — x402 settles a real $0.01 Base payment,
    # so it is suppressed under --quote-only (the live path keeps it on).
    x402_attempted = bool(settings.x402_enabled) and not dry_run
    x402_failed = False
    if settings.x402_enabled and not dry_run:
        try:
            from ictbot.data.x402_cmc import dex_search
            top = max(target, key=target.get) if target else "BNB"
            data = dex_search(top)
            if data and data.get("tks"):
                tk = data["tks"][0]
                x402_dex = {"q": top, "name": tk.get("n"), "symbol": tk.get("s"),
                            "price_usd": tk.get("pu"), "liquidity": tk.get("liq")}
        except Exception as e:
            x402_dex = None
            x402_failed = True
            print(f"[{_now()}] x402 read failed (non-fatal, enrichment only): {e}")
    # CMC on-chain (DEX) signals — buy/sell flow, unique traders, holder concentration, liquidity
    # depth + whale flow per token, harvested by the streamer's onchain feed. The full
    # market_signals buffet (flow_ratio, liquidity_usd, top10_pct, whale_net_usd, net_liquidity_usd,
    # unique_traders, volume_24h, ...) so the dashboard panel can show the complete picture.
    # Journaled for forward-validation + display; does NOT drive the trade (live-only signals).
    # Empty unless CMC_ONCHAIN_ENABLED and the feed is warm.
    onchain_signals = None
    if settings.cmc_onchain_enabled:
        try:
            from ictbot.strategy import market_signals as _ms
            onchain_signals = _ms.token_signals(list(active or CONTEST_TOKENS)) or None
        except Exception:
            onchain_signals = None
    entry = {
        "ts": _now(),
        # --quote-only emits REBALANCE_DRYRUN (execute=False): real balances + router quotes,
        # nothing signed. `tx` holds quote provider tags, not on-chain hashes — never a real fill.
        "event": "REBALANCE_DRYRUN" if dry_run else "REBALANCE",
        "dry_run": dry_run,
        "mode": mode,
        "strategy": strat_name,           # which registered strategy produced this tick
        "candle_source": candle_source,   # data provenance: cmc_4h | cmc_daily | binance_4h
        "quote_source": quote_source,     # tilt 7d source: cmc_ws (stream snapshot) | rest | None
        "nav_before": round(rep.nav_before, 2), "nav_after": round(rep.nav_after, 2),
        "dd_from_hwm": round(dd, 4), "fear_greed": fg, "fear_greed_available": fg is not None,
        "regime_score": round(score, 3) if score is not None else None,
        "deploy_cap": round(cap, 3),
        "target": {k: round(v, 4) for k, v in target.items()},
        "weights_after": {k: round(v, 4) for k, v in rep.weights_after.items() if v > 1e-4},
        "n_swaps": rep.n_swaps, "n_swaps_total": len(rep.swaps), "n_failed": rep.n_failed,
        "cumulative_swaps": cum, "trade_floor_min": settings.trade_floor_min,
        "fees_usd": round(rep.fees_usd, 4),
        "tx": [s.tx for s in rep.swaps if s.ok],
        "failed_swaps": [
            {"from": s.from_token, "to": s.to_token,
             "amount": round(s.amount_from, 6), "error": s.error}
            for s in rep.failed_swaps
        ],
        "rationale": rationale,
        "x402_dex": x402_dex,
        "x402_attempted": x402_attempted, "x402_failed": x402_failed,
        # UI token toggles — the universe this tick ranked over (audit trail).
        "active_tokens": active,
        # CMC Startup-tier enrichment (None / False unless the enhanced flags are on).
        "regime_terms": regime_terms,
        "btc_dominance": intel.btc_dominance if intel else None,
        "total_mktcap": intel.total_mktcap if intel else None,
        "fng_7d_avg": intel.fng_7d_avg if intel else None,
        "cmc_intel_used": intel is not None,
        "onchain_signals": onchain_signals,  # CMC onchain@* DEX flow/holders/liquidity (live-only)
        "cmc_rotation": cmc_rotation,  # CMC-native sector-rotation + multi-window momentum levers (live-only)
        "cmc_credits_today": _cmc_credits_today(),
        # CMC Agent Hub — the A/B-proven TA cap term + the market-overview skill read.
        "ta_health": round(ta_health, 4) if ta_health is not None else None,
        "ta_source": ta_source,
        "ta_rank_used": bool(ta_token_scores),
        "cmc_skill": skill,
        # PnL campaign — profit-lock ratchet status (additive; None when off).
        "profit_lock": ({
            "enabled": True,
            "armed": bool(state.get("profit_lock_armed")),
            "locked": bool(state.get("profit_locked")),
            "campaign_start_nav": round(float(state["campaign_start_nav"]), 2),
            "cum_ret": round(nav / float(state["campaign_start_nav"]) - 1.0, 4),
            "peak_since_trigger": (round(float(state["peak_since_trigger"]), 2)
                                   if state.get("peak_since_trigger") else None),
            "lock_floor": (round(float(state["lock_floor"]), 2)
                           if state.get("lock_floor") else None),
        } if settings.profit_lock_enabled and state.get("campaign_start_nav") else None),
    }
    # Pillar 3 — on-chain heartbeat (set_metadata) written BEFORE journaling so its result is
    # recorded in this tick's row (the dashboard reads `heartbeat` from the journal — key-free on
    # Render, unlike an on-chain read). write_heartbeat is best-effort + never raises; it returns
    # {ok, tx?, error?} (or None when skipped) instead of swallowing the reason. Suppressed under
    # --quote-only (a heartbeat is a real on-chain write).
    if settings.agent_heartbeat_enabled and settings.agent_id and not dry_run:
        from ictbot.agent import identity
        hb = identity.write_heartbeat(rationale, rep.nav_after)
        entry["heartbeat"] = hb
        if hb and hb.get("ok"):
            print(f"[{_now()}] heartbeat OK" + (f"  tx={str(hb.get('tx'))[:12]}…" if hb.get("tx") else ""))
        elif hb and not hb.get("ok"):
            err = str(hb.get("error") or "")
            print(f"[{_now()}] heartbeat FAILED (non-fatal): {err}")
            # Paymaster path: a 403 / sponsor / paymaster error almost always means the MegaFuel
            # sponsor policy isn't provisioned (or doesn't whitelist this wallet/contract). Point
            # the operator at the actionable readiness check instead of leaving a bare error.
            if settings.agent_use_paymaster and any(
                s in err.lower() for s in ("403", "sponsor", "paymaster", "not sponsorable")
            ):
                print(f"[{_now()}] hint: gasless sponsor likely unset — run `make heartbeat_check` "
                      f"(needs pm_isSponsorable=true; whitelist the registry + wallet on NodeReal).")
    journal(entry, mode)
    held = ", ".join(f"{k}={v:.0%}" for k, v in entry["weights_after"].items()) or "all USDT"
    reg = f"  regime={score:.2f} cap={cap:.2f}" if score is not None else f"  cap={cap:.2f}"
    print(f"[{_now()}] REBALANCE ({mode})  NAV {rep.nav_before:.2f}->{rep.nav_after:.2f}  "
          f"swaps={rep.n_swaps} fees=${rep.fees_usd:.2f}  held: {held}{reg}"
          + (f"  F&G={fg}" if fg is not None else ""))
    print(f"  \U0001f4ac {rationale}")
    return 0


def dd_watch(mode: str, dd_cap: float) -> int:
    """Idempotency wrapper for the fast DD-monitor (shares the daily tick's per-mode
    lock, so the two can never run against the book at the same time)."""
    fd = _acquire_lock(mode)
    if fd is None:
        print(f"[{_now()}] dd-watch SKIP: an allocator {mode} tick/watch is already "
              f"running (lock held).")
        return 2
    try:
        return _dd_watch(mode, dd_cap)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _dd_watch(mode: str, dd_cap: float) -> int:
    """FAST, flatten-only intraday drawdown monitor (reaction-time safety — finding G).

    Cron'd every few minutes during the contest window — far tighter than the once-daily
    rebalance — to bound how long an intraday NAV crash toward the 30% DQ line goes
    unreacted-to. DELIBERATELY minimal and strictly ONE-DIRECTIONAL: it reads prices +
    the PERSISTED high-water mark, and on a breach it ONLY flattens (token->USDT) and
    halts. It NEVER opens, flips, rebalances, recomputes the HWM, or runs the heavy
    candle/decision path — so it adds protection with zero overtrading / whipsaw risk.
    The heavy decision stays on the daily tick.

    Returns: 1 = flattened + halted; 0 = no action (within cap / already halted /
    no baseline HWM); 2 = skipped (preflight / lock / bad price — never a false flatten).
    """
    if mode == "live":
        rc = _live_preflight()
        if rc is not None:
            return rc
    state = load_state(mode)
    # Already halted by a prior tick/watch -> nothing to do (the daily tick refuses to
    # trade while halted, and the book is already flat).
    if state.get("halted"):
        print(f"[{_now()}] dd-watch: already HALTED; no action.")
        return 0
    if state.get("profit_locked"):
        print(f"[{_now()}] dd-watch: campaign PROFIT-LOCKED; book is flat. No action.")
        return 0
    # No persisted high-water mark yet (no daily tick has run) -> no baseline to measure
    # a drawdown against. The monitor must NOT seed one — that is the daily tick's job;
    # inventing an HWM here could mask a real drawdown. Skip cleanly.
    hwm = state.get("hwm")
    if not (hwm and hwm > 0):
        print(f"[{_now()}] dd-watch: no persisted HWM yet; nothing to guard.")
        return 0
    pf = price_fn(settings.cmc_api_key or None)
    broker, client = build_broker(mode, pf, state)
    # Same price-read + bad-price guards as the daily tick: a raised / zero / None price
    # must SKIP, never trigger a FALSE flatten (liquidation on bad data).
    try:
        prices = broker.prices()
    except RuntimeError as e:
        print(f"[{_now()}] dd-watch: price read failed ({e}); skipping (no false flatten).")
        return 2
    bad_px = [t for t, px in prices.items() if not (isinstance(px, (int, float)) and px > 0)]
    if bad_px:
        print(f"[{_now()}] dd-watch: invalid price(s) {bad_px}; skipping (no false flatten).")
        return 2
    nav = broker.nav(prices)
    if not (nav and nav > 0):
        print(f"[{_now()}] dd-watch: NAV={nav} invalid; skipping.")
        return 2
    dd = (hwm - nav) / hwm
    if dd > dd_cap:
        flat = broker.emergency_flatten(prices)
        state.update(halted=True, balances=client.balances())   # HWM kept as-is (persisted)
        save_state(state, mode)
        journal({"ts": _now(), "event": "DD_HALT", "mode": mode, "source": "dd_watch",
                 "nav": round(nav, 2), "hwm": round(hwm, 2), "dd": round(dd, 4),
                 "dd_cap": dd_cap, **_flatten_fields(flat)}, mode)
        print(f"[{_now()}] dd-watch DRAWDOWN HALT: dd={dd:.1%} > cap={dd_cap:.1%}. "
              f"Flattened {len(flat)} position(s). NAV={nav:.2f}.")
        return 1
    # Profit-lock ratchet, INTRADAY (campaign mode). Same one-directional contract
    # as the dd check above: arm / ratchet the peak / flatten on bank|trail — it
    # never opens, flips, or rebalances, and the bad-price guards above already
    # ensured this NAV is trustworthy. The watcher does NOT self-init the anchor
    # (that is the daily tick's job — mirroring the no-HWM-seeding rule above).
    if settings.profit_lock_enabled and state.get("campaign_start_nav"):
        pl_action, pl_upd = _profit_lock_eval(
            state, nav,
            trigger=settings.profit_lock_trigger, trail=settings.profit_lock_trail,
            min_keep=settings.profit_lock_min_keep, bank=settings.profit_lock_bank)
        cum_ret = nav / float(state["campaign_start_nav"]) - 1.0
        if pl_action in ("bank", "trail"):
            flat = broker.emergency_flatten(prices)
            state.update(pl_upd)
            state.update(profit_locked=True, balances=client.balances())
            save_state(state, mode)
            journal({"ts": _now(), "event": "PROFIT_LOCK", "mode": mode,
                     "source": "dd_watch", "kind": pl_action, "nav": round(nav, 2),
                     "campaign_start_nav": round(float(state["campaign_start_nav"]), 2),
                     "cum_ret": round(cum_ret, 4),
                     "peak_since_trigger": round(float(state.get("peak_since_trigger") or nav), 2),
                     "lock_floor": round(float(pl_upd.get("lock_floor") or 0.0), 2) or None,
                     **_flatten_fields(flat)}, mode)
            print(f"[{_now()}] dd-watch PROFIT LOCK ({pl_action}): cum={cum_ret:+.1%} "
                  f"NAV={nav:.2f}. Flattened {len(flat)} position(s); campaign banked.")
            return 1
        if pl_action == "arm":
            state.update(pl_upd)
            save_state(state, mode)
            journal({"ts": _now(), "event": "PROFIT_LOCK_ARMED", "mode": mode,
                     "source": "dd_watch", "nav": round(nav, 2),
                     "campaign_start_nav": round(float(state["campaign_start_nav"]), 2),
                     "cum_ret": round(cum_ret, 4),
                     "lock_floor": round(float(pl_upd["lock_floor"]), 2)}, mode)
            print(f"[{_now()}] dd-watch PROFIT LOCK ARMED: cum={cum_ret:+.1%} "
                  f"trailing floor={pl_upd['lock_floor']:.2f}.")
        elif pl_upd:
            state.update(pl_upd)          # peak/floor ratchet bookkeeping
            save_state(state, mode)
    print(f"[{_now()}] dd-watch OK: NAV={nav:.2f} dd={dd:.1%} <= cap={dd_cap:.1%}.")
    return 0


def daily_floor(mode: str) -> int:
    """Idempotency wrapper for the >=1-trade/day floor (shares the per-mode lock
    with the scheduled tick and the dd-watch, so it never races the book)."""
    fd = _acquire_lock(mode)
    if fd is None:
        print(f"[{_now()}] daily-floor SKIP: an allocator {mode} tick/watch is "
              f"already running (lock held).")
        return 2
    try:
        return _daily_floor(mode)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _daily_floor(mode: str, now: datetime | None = None) -> int:
    """Contest >=1-trade/DAY floor (the brief's other minimum, alongside >=7/week).

    Cron'd once near end-of-day UTC during the contest window: if the day is
    about to close with ZERO successful swaps (journal-counted), bank ONE
    ~0-NAV-impact round-trip via the same bounded nudge the weekly floor uses.
    Gates: TRADE_FLOOR_DAILY on · inside [contest_start, contest_end] · past
    trade_floor_daily_deadline_utc · not halted (a DD halt outranks the floor —
    never re-open risk just for a trade count). Works while profit-locked (the
    book is USDT-rich, the nudge is flat). Returns 1 = banked, 0 = no-op,
    2 = skipped/could-not-bank.
    """
    if not settings.trade_floor_daily:
        print(f"[{_now()}] daily-floor: TRADE_FLOOR_DAILY off; nothing to do.")
        return 0
    now = now or datetime.now(timezone.utc)
    try:
        start = datetime.fromisoformat(settings.contest_start).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(settings.contest_end).replace(tzinfo=timezone.utc)
    except Exception:
        print(f"[{_now()}] daily-floor: bad contest dates; skipping.")
        return 2
    if not (start <= now <= end):
        print(f"[{_now()}] daily-floor: outside the contest window; no-op.")
        return 0
    if now.hour < int(settings.trade_floor_daily_deadline_utc):
        print(f"[{_now()}] daily-floor: before {settings.trade_floor_daily_deadline_utc:02d}:00 UTC; no-op.")
        return 0
    if mode == "live":
        rc = _live_preflight()
        if rc is not None:
            return rc
    state = load_state(mode)
    if state.get("halted"):
        print(f"[{_now()}] daily-floor: HALTED (DD breach outranks the floor); no trade.")
        return 0
    done = _swaps_today(mode, now)
    if done > 0:
        print(f"[{_now()}] daily-floor: {done} swap(s) already banked today; no-op.")
        return 0
    pf = price_fn(settings.cmc_api_key or None)
    broker, client = build_broker(mode, pf, state)
    # Same bad-price guards as the tick/watch: never trade on bad data.
    try:
        prices = broker.prices()
    except RuntimeError as e:
        print(f"[{_now()}] daily-floor: price read failed ({e}); skipping.")
        return 2
    bad_px = [t for t, px in prices.items() if not (isinstance(px, (int, float)) and px > 0)]
    if bad_px:
        print(f"[{_now()}] daily-floor: invalid price(s) {bad_px}; skipping.")
        return 2
    swaps, banked = _floor_nudge(broker, prices, 1, state)
    cum = int(state.get("cumulative_swaps", 0)) + banked
    if banked:
        state.update(balances=client.balances(), cumulative_swaps=cum)
        save_state(state, mode)
        journal({"ts": _now(), "event": "FLOOR_NUDGE", "mode": mode, "daily": True,
                 "banked": banked, "cumulative_swaps": cum,
                 "tokens": _nudged_tokens(swaps, broker.quote),
                 "tx": [s.tx for s in swaps if s.ok]}, mode)
        print(f"[{_now()}] daily-floor FLOOR_NUDGE: banked {banked} trade(s) -> "
              f"cum={cum} (>=1/day floor).")
        return 1
    journal({"ts": _now(), "event": "FLOOR_NUDGE_FAILED", "mode": mode, "daily": True,
             "cumulative_swaps": cum, "need": 1,
             "trade_floor_min": settings.trade_floor_min,
             "n_attempted": len(swaps),
             "errors": [s.error for s in swaps if not s.ok][:3]}, mode)
    print(f"[{_now()}] daily-floor WARNING: zero swaps today and could not bank a "
          f"nudge (insufficient {broker.quote}).")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sim", "live"], default=settings.twak_mode)
    ap.add_argument("--dd-cap", type=float, default=settings.max_drawdown_frac)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval-min", type=float, default=5.0)
    ap.add_argument("--ticks", type=int, default=1, help="max ticks when --loop")
    ap.add_argument("--reset", action="store_true", help="wipe state/HWM for --mode (sim|live)")
    ap.add_argument("--resume", action="store_true",
                    help="clear a prior drawdown HALT for --mode so trading resumes")
    ap.add_argument("--force", action="store_true",
                    help="with --resume: clear the halt even if the last flatten was PARTIAL "
                         "(acknowledge possible residual on-chain exposure)")
    ap.add_argument("--dd-watch", action="store_true",
                    help="FAST flatten-only intraday risk monitor: drawdown halt + "
                         "profit-lock ratchet (never opens/flips/rebalances). Cron "
                         "tighter than the scheduled tick.")
    ap.add_argument("--anchor-nav", type=float, default=None,
                    help="one-shot: set the profit-lock campaign anchor NAV for "
                         "--mode (cum return = NAV/anchor - 1) and exit. Re-run "
                         "after any --reset (which wipes it).")
    ap.add_argument("--unlock-profit", action="store_true",
                    help="clear a profit-lock (armed + locked) for --mode so trading "
                         "resumes; keeps the campaign anchor. After a +bank lock you "
                         "normally STAY flat — unlock deliberately.")
    ap.add_argument("--ensure-daily-floor", action="store_true",
                    help="contest >=1-trade/day floor: bank ONE ~0-impact round-trip "
                         "if today (UTC) has zero swaps. Cron near end-of-day during "
                         "the contest window (TRADE_FLOOR_DAILY=true).")
    ap.add_argument("--preflight-only", action="store_true",
                    help="LIVE dry-run: validate creds + ENABLE_LIVE_TRADING + the resolved "
                         "strategy, then EXIT without ticking or executing any swap. Lets an "
                         "operator confirm a live promotion (STRATEGY_NAME) is armed safely.")
    ap.add_argument("--quote-only", action="store_true",
                    help="QUOTE-ONLY integration track: run the FULL loop against the real twak "
                         "CLI (real on-chain balances + router quotes) with execute=False — nothing "
                         "is signed or spent, no creds/ENABLE_LIVE_TRADING needed. Writes to the "
                         "separate allocator_dryrun.* files. Drop this flag at contest start to "
                         "execute for real.")
    ap.add_argument("--strategy", default=None,
                    help="select the registered arm to run THIS invocation (e.g. momentum_cmc, "
                         "mean_reversion), overriding .env STRATEGY_NAME — the clean operator live-arm "
                         "switch. LIVE refuses an arm whose survival gate FAILED (DQ-unsafe); a "
                         "forward-not-eligible arm runs with a loud warning. Unset = the configured default.")
    args = ap.parse_args()
    # --quote-only is an internal 'dryrun' mode: live CLI client, quote-only swaps, isolated
    # journal/state. Overrides --mode so every downstream path (tick/reset/preflight) is sandboxed.
    if args.quote_only:
        args.mode = "dryrun"
    # Clean live-arm switch: --strategy NAME selects the arm for THIS run without editing .env.
    # Authoritative for LIVE because _resolve_strategy_name returns settings.strategy_name (it
    # ignores the dashboard selector for live). Operator-only (CLI/cron) — the dashboard still can't
    # change the live arm. Gate-guarded in _tick/_preflight so a DQ-unsafe arm can't be armed.
    if args.strategy:
        settings.strategy_name = args.strategy

    if args.reset:
        sp = state_path(args.mode)
        if sp.exists():
            sp.unlink()
        print(f"{args.mode} state reset (ledger + HWM cleared: {sp.name}). "
              f"NOTE: the profit-lock campaign anchor is wiped too — re-run --anchor-nav.")
        return 0

    if args.anchor_nav is not None:
        if not args.anchor_nav > 0:
            print(f"--anchor-nav must be > 0; got {args.anchor_nav}")
            return 2
        st = load_state(args.mode)
        st["campaign_start_nav"] = float(args.anchor_nav)
        save_state(st, args.mode)
        journal({"ts": _now(), "event": "CAMPAIGN_ANCHOR", "mode": args.mode,
                 "campaign_start_nav": float(args.anchor_nav), "source": "cli"}, args.mode)
        print(f"{args.mode} campaign anchor set: NAV {args.anchor_nav:.2f} "
              f"(profit lock arms at {settings.profit_lock_trigger:+.0%}, "
              f"banks at {settings.profit_lock_bank:+.0%}).")
        return 0

    if args.unlock_profit:
        st = load_state(args.mode)
        was = bool(st.get("profit_locked"))
        st["profit_locked"] = False
        st["profit_lock_armed"] = False
        st.pop("peak_since_trigger", None)
        st.pop("lock_floor", None)
        save_state(st, args.mode)
        journal({"ts": _now(), "event": "PROFIT_UNLOCK", "mode": args.mode,
                 "was_locked": was}, args.mode)
        print(f"{args.mode} profit lock {'cleared (was LOCKED)' if was else 'already clear'}; "
              f"next tick will trade. Anchor kept: {st.get('campaign_start_nav')}")
        return 0

    if args.resume:
        st = load_state(args.mode)
        was = bool(st.get("halted"))
        # Residual-exposure guard: if the last halt's emergency-flatten left a failed sell leg, the
        # book may still hold exposure — resuming would trade on top of it. Require --force to ack.
        partial = _last_halt_partial(args.mode)
        if was and partial and not args.force:
            print(f"[{_now()}] {args.mode} --resume BLOCKED: the last halt's flatten was PARTIAL "
                  f"(ok={partial['flattened_ok']}/{partial['attempted']}, errors={partial['errors']}). "
                  f"Residual on-chain exposure may remain — verify the book is flat, then re-run with "
                  f"--force.")
            return 2
        st["halted"] = False
        save_state(st, args.mode)
        extra = (" NOTE: profit lock still set — use --unlock-profit to re-open a "
                 "banked campaign." if st.get("profit_locked") else "")
        forced = " (--force: partial-flatten residual acknowledged)" if (was and partial) else ""
        print(f"{args.mode} halt {'cleared (was HALTED)' if was else 'already clear'}; "
              f"next tick will trade. HWM={st.get('hwm')}{extra}{forced}")
        return 0

    if args.ensure_daily_floor:
        return daily_floor(args.mode)

    if args.preflight_only:
        # Validate the LIVE setup (creds + ENABLE_LIVE_TRADING) and which arm would run, then
        # EXIT before any broker is built or any swap is signed. The safe way to confirm a
        # promotion is armed. _live_preflight() returns None when OK, else an exit code.
        strat_name = _resolve_strategy_name("live")
        rc = _live_preflight(dry_run=(args.mode == "dryrun"))
        if rc is not None:
            print(f"[{_now()}] preflight-only: LIVE setup NOT ready (rc={rc}); resolved "
                  f"strategy '{strat_name}'. No broker built, no swap executed.")
            return rc
        # The chosen arm must clear the survival gate before it can be armed (warns on forward-pending).
        gate_rc = _assert_live_arm_gate(strat_name)
        if gate_rc is not None:
            print(f"[{_now()}] preflight-only: arm '{strat_name}' BLOCKED by the survival gate. "
                  f"No broker built, no swap executed.")
            return gate_rc
        print(f"[{_now()}] preflight-only: LIVE setup OK — would run strategy '{strat_name}'. "
              f"No broker built, no swap executed.")
        return 0

    # Fast flatten-only monitor vs the heavy daily rebalance — same loop plumbing.
    runner = dd_watch if args.dd_watch else tick
    if not args.loop:
        return runner(args.mode, args.dd_cap)

    rc = 0
    for n in range(args.ticks):
        rc = runner(args.mode, args.dd_cap)
        if rc == 1:                       # halted
            break
        if n < args.ticks - 1:
            time.sleep(args.interval_min * 60)
    return rc


if __name__ == "__main__":
    sys.exit(main())
