"""
Background scanner. Iterates over every pair in PAIRS forever,
calling analyze_pair() and routing each BUY/SELL through the
SignalRouter (Strategy → CapGate → Broker).

D1 (ROADMAP §D1): structured JSON logging + Prometheus metrics so
the catalogue in runtime.metrics is no longer dead code.

C2 follow-up (ROADMAP §C2): wire SignalRouter so paper-broker fills
actually happen on each iteration. The router consults the cap gate
(MaxOpenPositions / DailyLossLimit / MaxDrawdown), records placements
in `data/journal/signals.json`, and routes either a paper broker
(default) or the live broker when ENABLE_LIVE_TRADING + allowed_pairs
both clear.

Run with:  python -m ictbot.orchestrator.scanner
"""

import os
import time

from ictbot.exec.factory import LiveTradingDisabled, build_live_broker
from ictbot.exec.paper import PaperBroker
from ictbot.orchestrator.analyzer import analyze_pair
from ictbot.orchestrator.router import SignalRouter
from ictbot.portfolio.account import Account
from ictbot.portfolio.caps import (
    CapGate,
    DailyLossLimit,
    MaxConcurrentSameDirection,
    MaxDrawdown,
    MaxLiveTradesPerDay,
    MaxOpenPositions,
    NearPriceDedup,
    NewsBlackoutCap,
)
from ictbot.portfolio.journal import append_signal
from ictbot.runtime import heartbeat, kill_switch, metrics, pause
from ictbot.runtime.logger import get_json_logger, get_logger
from ictbot.runtime.session_gate import decide_notify
from ictbot.runtime.sessions import is_killzone_active
from ictbot.runtime.signal_memory import load_last_near_miss, save_last_near_miss
from ictbot.settings import (
    AUTO_EXECUTE_MIN_CONFIDENCE,
    DAILY_LOSS_LIMIT_R,
    MAX_DRAWDOWN_FRAC,
    MAX_LIVE_TRADES_PER_DAY,
    MAX_OPEN_POSITIONS,
    MAX_SAME_DIRECTION,
    NEAR_PRICE_DEDUP_BPS,
    NEAR_PRICE_DEDUP_WINDOW_S,
    NEWS_BLACKOUT_COUNTRIES,
    NEWS_BLACKOUT_IMPACTS,
    NEWS_BLACKOUT_MINUTES,
    PAIRS,
    RISK_PCT,
    RISK_PCT_LIVE,
    SHADOW_MODE,
    TELEGRAM_TOKEN,
    TG_COMMANDS_MODE,
    TG_CONFIRM_MODE,
    TG_CONFIRM_TIMEOUT_S,
    TG_HEARTBEAT_EVERY_N_CYCLES,
    TG_IN_SESSION_ONLY,
    TG_MIN_CONFIDENCE_BYPASS,
    TG_OPERATOR_USER_ID,
    settings,
)

# Module-level handle for the TG confirm service. Constructed in main()
# only when TG_CONFIRM_MODE=true; _route_signal reads it to decide
# between direct routing and confirm-then-fire.
_tg_confirm = None

log = get_logger("scanner")
jlog = get_json_logger("scanner")

# Audit gap #8: per-pair memory of the last *closed-bar* timestamp we
# evaluated. sleep(30) on 60s bars otherwise re-evaluates the same
# in-progress bar twice with drifting prices. Module-level so a
# scanner restart resets it (intentional — fresh process, fresh state).
_last_seen_bar: dict[str, object] = {}


# Canonical pipeline ordering for the funnel counter. Earlier = more
# upstream. Each blocker text emitted by ict_pro_max._diagnose() is
# scanned in this order so we count the FIRST drop-off per eval — a
# single eval that fails three gates is one funnel entry, not three.
# The "gate" bucket catches the environmental gates (killzone / regime
# / news) which short-circuit before any ICT blocker is recorded.
_STEP_ORDER: tuple[str, ...] = (
    "htf_bias",
    "bias_align",
    "poi_tap",
    "mss",
    "fvg",
    "mfvg_retest",
    "delta",
    "gate",
)


def _blocker_to_step(blocker: str) -> str | None:
    """Map a diagnostic blocker string to its canonical pipeline step.

    Blocker phrasing is owned by `ict_pro_max._diagnose()`; this map only
    pattern-matches enough of each string to be robust to minor wording
    changes. Returns None for an unrecognised blocker so the caller can
    log & skip rather than mis-attribute to the wrong bucket.
    """
    if not blocker:
        return None
    b = blocker.lower()
    # Phase E — bias alignment ranks above POI/MSS because it's an
    # HTF-layer concern, but below "wrong HTF bias" (which is a more
    # fundamental rejection). Check before the generic "htf bias" match
    # since the alignment blocker also mentions HTF.
    if "bias mismatch" in b:
        return "bias_align"
    if "htf bias" in b:
        return "htf_bias"
    if "poi not tapped" in b or "poi tapped" in b:
        return "poi_tap"
    if "mfvg not retested" in b:
        # Check before "mss" because the MFVG-retest text contains "mss"
        # in neither form, but ordering still matters if future copy
        # changes — the more specific match wins.
        return "mfvg_retest"
    if "mss" in b:
        return "mss"
    if "fvg" in b:
        return "fvg"
    if "delta" in b:
        return "delta"
    return None


def _first_funnel_step(result: dict) -> str | None:
    """Return the canonical step where this eval first dropped off, or None
    if the eval actually fired (BUY/SELL) or carries no diagnostic.

    Order of precedence:
      1. `gate_blocked` — environmental gate short-circuited the eval.
      2. The first blocker in `diagnostics.blockers` whose canonical step
         is highest in `_STEP_ORDER`.
    """
    if result.get("entry") in ("BUY", "SELL"):
        return None
    if result.get("gate_blocked"):
        return "gate"

    diag = result.get("diagnostics") or {}
    blockers = diag.get("blockers") or []
    seen: set[str] = set()
    for raw in blockers:
        step = _blocker_to_step(raw)
        if step is not None:
            seen.add(step)
    if not seen:
        return None
    # Walk the canonical order and return the first step that's present.
    for step in _STEP_ORDER:
        if step in seen:
            return step
    return None


def _emit_funnel(result: dict) -> None:
    """Increment the funnel counter + emit a structured log line for the
    first drop-off step in this eval. No-op when the eval fired or when
    no recognisable blocker is present (avoids polluting the counter with
    unmapped strings)."""
    step = _first_funnel_step(result)
    if step is None:
        return
    pair = result.get("pair", "?")
    diag = result.get("diagnostics") or {}
    direction = diag.get("closest_direction") or "?"
    metrics.funnel_step_failures_total.labels(pair=pair, step=step, direction=direction).inc()
    jlog.info(
        "funnel_step_failed",
        extra={
            "pair": pair,
            "step": step,
            "direction": direction,
            "confidence": int(result.get("confidence", 0) or 0),
        },
    )


def _make_caps(account: Account, *, include_live_caps: bool = False) -> CapGate:
    """Standard cap stack: 1 open position, 1R daily loss, 5% max DD.

    Phase D: when `include_live_caps=True`, also appends
    MaxLiveTradesPerDay + (optionally) NewsBlackoutCap. Off by default so
    the shadow leg and paper-only paths don't get an extra rate-limit on
    top of their existing caps.
    """
    caps: list = [
        MaxOpenPositions(max_open=MAX_OPEN_POSITIONS),  # Fix 5.H — env-configurable
        # Fix 9.B (plan: Phase 9): anti-correlation gate. With
        # MAX_OPEN_POSITIONS raised to 3, prevent 3 same-side stacks on
        # correlated crypto pairs (the failure mode if BTC/XRP/PAXG all
        # fire SELL in a crypto-wide downtrend). 0 disables.
        MaxConcurrentSameDirection(max_same=MAX_SAME_DIRECTION),
        # Fix 13.A (plan: Phase 13): env-configurable daily R loss cap.
        # Default 1.0 historical; boot guard refuses on <= 0.
        DailyLossLimit(limit_R=DAILY_LOSS_LIMIT_R),
        # Fix 13.B (plan: Phase 13): env-configurable max drawdown.
        # Default 0.05 historical; boot guard refuses outside (0, 1).
        MaxDrawdown(account=account, limit=MAX_DRAWDOWN_FRAC),
    ]
    if include_live_caps:
        caps.append(MaxLiveTradesPerDay(limit=MAX_LIVE_TRADES_PER_DAY))
        # Phase 14 — Near-price dedup. Reads from the same journal as
        # MaxLiveTradesPerDay; 0 on either knob disables. Placed last so
        # the cheaper-to-evaluate caps (open count, side, drawdown) get
        # to short-circuit first when they fire — only signals that
        # would otherwise route reach the journal scan.
        caps.append(
            NearPriceDedup(
                threshold_bps=NEAR_PRICE_DEDUP_BPS,
                window_seconds=NEAR_PRICE_DEDUP_WINDOW_S,
            )
        )
        if NEWS_BLACKOUT_MINUTES > 0:
            caps.append(
                NewsBlackoutCap(
                    window_minutes=NEWS_BLACKOUT_MINUTES,
                    countries=NEWS_BLACKOUT_COUNTRIES,
                    impacts=NEWS_BLACKOUT_IMPACTS,
                )
            )
    return CapGate(caps)


def _build_router():
    """Construct the broker + caps the scanner will route through.

    Picks the live broker (Delta or Binance, per `settings.exchange`) only
    when BOTH the env-level kill switch is off AND ENABLE_LIVE_TRADING is
    true. The live broker also enforces its own gate so a bug in this
    factory can't bypass it.

    Phase B: when `SHADOW_MODE=true` AND we're running live, wraps the
    live router in a ShadowRouter that runs a parallel PaperBroker leg
    on every signal. The shadow leg uses its own Account + CapGate so a
    paper fill doesn't artificially block live execution.
    """
    live = settings.enable_live_trading and not kill_switch.is_engaged()
    broker = build_live_broker(allowed_pairs=set(PAIRS)) if live else PaperBroker()
    # Fix 2.I (plan: live P&L clean-up follow-up): if the live broker
    # exposes on_reconnect (BinanceLiveBroker, DeltaLiveBroker, etc.),
    # call it to repopulate `self._orders` from `fetch_positions` so the
    # MaxOpenPositions(1) cap correctly sees pre-existing positions on
    # restart. Without this, a scanner restart with an open position
    # silently bypasses the cap on the next fire and causes orphan
    # doubling (see 2026-06-05 PAXG incident). Wrapped in try/except so
    # a transient ccxt failure can't block scanner startup; partial
    # reconcile still beats forgotten state.
    if live and hasattr(broker, "on_reconnect"):
        try:
            broker.on_reconnect()
        except Exception as exc:  # noqa: BLE001
            log.warning("broker.on_reconnect() failed: %s", exc)

    # Fix 9.E (plan: Phase 9): boot-time per-pair readiness gate. Asks
    # the broker to verify every configured pair has: requested
    # leverage, ISOLATED margin, a live ticker, and a min-notional that
    # the current risk size can actually meet. Refuses to start under
    # STRICT_PAIR_INIT=true if any pair fails — better to fail loud at
    # boot than to silently skip a pair every cycle for hours. Banner
    # writes one line per pair so ops can see the readiness without
    # tailing JSON logs.
    if live and hasattr(broker, "verify_all_pairs_ready"):
        try:
            statuses = broker.verify_all_pairs_ready()
        except Exception as exc:  # noqa: BLE001
            log.warning("verify_all_pairs_ready failed: %s", exc)
            statuses = {}
        if statuses:
            failed: list[str] = []
            print("pair readiness:")
            for pair, st in statuses.items():
                lev = st.get("leverage")
                mm = st.get("margin_mode") or "?"
                tk = st.get("ticker_price")
                tk_s = f"${tk:,.2f}" if isinstance(tk, (int, float)) and tk else "?"
                mn = st.get("min_notional") or 0
                qty = st.get("sized_qty")
                qty_s = f"{qty:g}" if qty else "?"
                ok = "OK" if st.get("ok") else "FAIL"
                print(
                    f"  {pair:<22} lev={lev}x margin={mm} ticker={tk_s} "
                    f"min_notional=${mn:.2f} sized_qty={qty_s} {ok}"
                )
                if not st.get("ok"):
                    reasons = ", ".join(st.get("reasons") or [])
                    failed.append(f"{pair} ({reasons})")
            if failed and getattr(settings, "strict_pair_init", True):
                raise RuntimeError(
                    "Phase 9.E boot gate: pair readiness check failed for "
                    f"{len(failed)} pair(s): {'; '.join(failed)}. Refusing "
                    "to start; set STRICT_PAIR_INIT=false to override (not "
                    "recommended)."
                )
    # Audit gap #1: Account is now the single equity source. MaxDrawdown
    # reads from it; DailyLossLimit reads from the close-callback path
    # the router wires up below.
    account = Account(starting_balance=10_000.0)
    live_router = SignalRouter(
        broker=broker,
        cap_gate=_make_caps(account, include_live_caps=live),
        balance=10_000.0,
        # Fix 2.D (plan: live P&L clean-up): RISK_PCT_LIVE now wins
        # whenever the router is live, regardless of SHADOW_MODE. The
        # pre-fix `(live and SHADOW_MODE)` gate meant that running live
        # WITHOUT shadow silently fell back to the 10x-larger RISK_PCT,
        # and the MAX_LIVE_RISK_PER_TRADE_PCT boot guard (which only
        # protects RISK_PCT_LIVE) didn't catch it. The shadow leg
        # below constructs its OWN router with RISK_PCT, so the shadow
        # comparison still works.
        risk_pct=RISK_PCT_LIVE if live else RISK_PCT,
        journal=append_signal,
        account=account,
        # Phase D: only the genuinely-live router increments
        # ictbot_live_trades_total. Paper mode + shadow leg below stay
        # is_live=False so the counter reflects real exchange fills.
        is_live=live,
    )

    if not (live and SHADOW_MODE):
        return live_router

    # ---- Phase B: shadow leg --------------------------------------------
    from ictbot.orchestrator.shadow_router import ShadowRouter

    shadow_account = Account(starting_balance=10_000.0)
    shadow_router_inner = SignalRouter(
        broker=PaperBroker(),
        cap_gate=_make_caps(shadow_account),
        balance=10_000.0,
        risk_pct=RISK_PCT,
        # Shadow leg writes nothing to disk — its closes still flow
        # through the cap layer + Account, and the comparison metrics
        # capture the data the report needs. Keeps the live journal
        # the single source of truth on disk for now.
        journal=None,
        account=shadow_account,
    )
    return ShadowRouter(live_router=live_router, shadow_router=shadow_router_inner)


def _evaluate_with_metrics(pair: str) -> dict:
    """Wrap analyze_pair so latency + outcome metrics are emitted.

    J8 (audit gap #16): an exception from analyze_pair previously left
    the eval uncounted. Now every code path emits exactly one
    `evaluations_total` increment with an outcome label."""
    try:
        with metrics.evaluate_latency_seconds.time():
            r = analyze_pair(pair, notify=True)
    except Exception as exc:
        metrics.evaluations_total.labels(pair=pair, outcome="error").inc()
        log.exception("analyze_pair raised for %s: %s", pair, exc)
        return {
            "pair": pair,
            "error": f"analyze_pair raised: {exc}",
            "entry": "NO ENTRY",
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
        }

    if r.get("error"):
        metrics.evaluations_total.labels(pair=pair, outcome="error").inc()
        return r

    if r.get("entry") in ("BUY", "SELL"):
        metrics.evaluations_total.labels(pair=pair, outcome="signal").inc()
        metrics.signals_fired_total.labels(pair=pair, direction=r["entry"]).inc()
    else:
        metrics.evaluations_total.labels(pair=pair, outcome="no_entry").inc()
        # Per-step funnel — record where this no-entry eval dropped off so
        # the dashboard can show "of N evals on BTC, X were blocked at
        # mss / Y at fvg". Only emitted on no_entry (signals already
        # counted by signals_fired_total above).
        _emit_funnel(r)
    return r


def _settle_broker_on_last_closed_bar(router: SignalRouter, pair: str, result: dict) -> None:
    """Audit gap #2 + #7: hand the broker the last CLOSED 1m bar so paper
    TP/SL fills + live position reconciliation happen each iteration.

    Uses iloc[-2] because ccxt's fetch_ohlcv tail row is the still-forming
    current bar — settling against that bar's running high/low produces
    phantom wick-triggered closes.
    """
    ltf_df = result.get("ltf_df")
    if ltf_df is None or len(ltf_df) < 2:
        return
    closed_bar = ltf_df.iloc[-2]
    bar = {
        "time": closed_bar["time"],
        "open": float(closed_bar["open"]),
        "high": float(closed_bar["high"]),
        "low": float(closed_bar["low"]),
        "close": float(closed_bar["close"]),
        "volume": float(closed_bar["volume"]),
    }
    try:
        closed_orders = router.broker.on_bar(pair, bar)
    except Exception as exc:  # noqa: BLE001 — broker bugs must not kill the loop
        log.warning("broker.on_bar(%s) raised: %s", pair, exc)
        return
    for o in closed_orders or []:
        log.info(
            "CLOSED %s %s @ %s (%s, R=%s)",
            o.side,
            o.pair,
            o.close_price,
            o.close_reason,
            o.realised_pnl_R(),
        )
        jlog.info(
            "order_closed",
            extra={
                "pair": o.pair,
                "side": o.side,
                "close_price": o.close_price,
                "close_reason": o.close_reason,
                "realised_R": o.realised_pnl_R(),
            },
        )


def _notify_near_miss(result: dict) -> None:
    """Send a one-line TG alert when a pair is one or two confidence bits
    away from a real BUY/SELL. Dedup'd per (pair, would-be direction,
    primary blocker) — only refires when the close-but-no-cigar story
    materially changes, so the bot can't spam a chat that's already
    been told 'BTC needs MSS to fire'.

    Session gate: outside London/NY killzones, near-misses are suppressed
    unless this pair's confidence cleared the bypass threshold — then we
    send with the off-session disclaimer prepended. The dedup state is
    only written when we ACTUALLY sent something, so a suppressed message
    will refire next cycle if it still qualifies.
    """
    diag = result.get("diagnostics") or {}
    pair = result.get("pair", "?")
    direction = diag.get("closest_direction") or "?"
    blockers = diag.get("blockers") or []
    primary_blocker = blockers[0] if blockers else "—"

    # State key: same pair + same would-be direction + same primary
    # blocker = nothing new to say. The blocker text is short and
    # bounded by analyzer._diagnose so it's safe to use as a hash input.
    state_key = f"{pair}|{direction}|{primary_blocker}"
    last = load_last_near_miss()
    if last.get(pair) == state_key:
        return

    confidence = int(result.get("confidence", 0) or 0)
    in_session = is_killzone_active()
    send_ok, prefix = decide_notify(
        in_session=in_session,
        confidence=confidence,
        in_session_only=TG_IN_SESSION_ONLY,
        min_confidence_bypass=TG_MIN_CONFIDENCE_BYPASS,
    )
    if not send_ok:
        # Off-session + below bypass — silence. Don't update the dedup
        # state so the alert can still fire when session opens.
        return

    arrow = "🟢" if direction == "BUY" else "🔴"
    body = (
        f"⚠️ NEAR-MISS  {pair}\n"
        f"{arrow} would-be: {direction}  ({confidence}% conf)\n"
        f"   price: {result.get('price')}\n"
        f"   htf: {result.get('htf_bias')}  ·  ltf: {result.get('ltf_bias')}\n"
        f"   poi: {result.get('poi_tap')}  ·  mss: {result.get('ltf_mss')}  ·  fvg: {result.get('fvg')}\n"
        f"   blocker: {primary_blocker}"
    )
    msg = prefix + body
    try:
        from ictbot.notify.telegram import send_telegram

        if send_telegram(msg):
            last[pair] = state_key
            save_last_near_miss(last)
    except Exception as exc:  # noqa: BLE001 — TG must never break the scan loop
        log.warning("near-miss TG failed (continuing): %s", exc)


def _route_signal(router, result: dict) -> None:
    """Push a BUY/SELL through CapGate → broker. Failures are logged but
    never kill the scan loop — a single bad order shouldn't take down
    every other pair's evaluation.

    Phase D: 3-tier decision at the top.
      • `conf >= AUTO_EXECUTE_MIN_CONFIDENCE` (default 100) → AUTO: fall
        through to `router.route()` exactly as before.
      • `conf <  threshold` AND `TG_CONFIRM_MODE=true` → CONFIRM: DM the
        operator with `[✅ Trade] [❌ Skip]` buttons; trade only fires
        if they click Trade within `TG_CONFIRM_TIMEOUT_S`.
      • else → DROP: log + return without placing or DMing. Prevents
        low-conviction signals from auto-firing AND from spamming TG
        when confirm mode is off.
    Scanner returns immediately in CONFIRM/DROP so the next pair is
    evaluated without waiting for the click.
    """
    conf = int(result.get("confidence") or 0)
    pair = result.get("pair")

    if conf >= AUTO_EXECUTE_MIN_CONFIDENCE:
        tier = "auto"
    elif TG_CONFIRM_MODE and _tg_confirm is not None:
        tier = "confirm"
    else:
        tier = "drop"

    if tier == "drop":
        jlog.info(
            "tier_drop",
            extra={
                "pair": pair,
                "confidence": conf,
                "threshold": AUTO_EXECUTE_MIN_CONFIDENCE,
                "tg_confirm_mode": TG_CONFIRM_MODE,
            },
        )
        return

    if tier == "confirm":
        try:
            sid = _tg_confirm.send_signal_with_buttons(result)
            log.info("tg confirm: queued %s for operator (conf=%s)", sid, conf)
            jlog.info(
                "tg_confirm_queued",
                extra={"signal_id": sid, "pair": pair, "confidence": conf},
            )
        except Exception as exc:  # noqa: BLE001 — TG must not break the scan loop
            log.warning("tg confirm send failed (continuing): %s", exc)
        return

    # tier == "auto" — direct routing path.
    try:
        outcome = router.route(result)
    except LiveTradingDisabled as exc:
        log.warning("live gate refused: %s", exc)
        jlog.warning("live_gate_refused", extra={"reason": str(exc)})
        return
    except Exception as exc:
        log.exception("router error: %s", exc)
        jlog.exception("router_error", extra={"error": str(exc)})
        return

    if outcome.placed:
        log.info(
            "PLACED %s %s qty=%s",
            outcome.order.side,
            outcome.order.pair,
            outcome.order.qty,
        )
        jlog.info(
            "order_placed",
            extra={
                "pair": outcome.order.pair,
                "side": outcome.order.side,
                "qty": outcome.order.qty,
                "entry": outcome.order.entry,
                "sl": outcome.order.sl,
                "tp": outcome.order.tp,
            },
        )
    elif outcome.rejection is not None:
        metrics.cap_rejections_total.labels(cap=outcome.rejection.reason.split()[0]).inc()
        jlog.info(
            "cap_rejected",
            extra={"pair": result.get("pair"), "reason": outcome.rejection.reason},
        )


def main():
    log.info(f"ICT AI BOT PRO MAX scanner started for {len(PAIRS)} pairs.")
    if metrics.is_available():
        metrics.start_metrics_server(port=9100)
        log.info("Prometheus /metrics on :9100 (prometheus_client available).")
        jlog.info("metrics_server_started", extra={"port": 9100})
    else:
        log.info("prometheus_client not installed — metrics are no-ops.")

    # Hosts like Render require the process to bind $PORT shortly after
    # boot or they consider the deploy unhealthy and recycle it. Even
    # when PORT isn't set (local dev), the small health server is cheap
    # to run and useful for external pingers (UptimeRobot/cron-job.org)
    # that keep a free-tier web service from sleeping after 15 min idle.
    health_port_str = os.environ.get("PORT")
    if health_port_str:
        from ictbot.runtime.health_server import start_health_server

        try:
            start_health_server(port=int(health_port_str))
            log.info("health endpoint live at /, /health, /healthz on :%s", health_port_str)
        except Exception as exc:
            # Port-bind failures must not kill the scanner — the bot's
            # primary job is TG signals, not serving HTTP.
            log.warning("health server failed to start on :%s: %s", health_port_str, exc)

    router = _build_router()
    log.info(
        "router using broker=%s cap_gate=%d caps",
        router.broker.name,
        len(router.cap_gate.caps),
    )

    # Phase C/D: spin up the TG service if EITHER confirm-mode or
    # commands-mode is on. A single PTB Application handles both: the
    # confirm-button callback queries and the slash-command handlers.
    # on_confirm captures the router built above so a click invokes the
    # exact same code path the auto-route flow would have used.
    global _tg_confirm
    if TG_CONFIRM_MODE or TG_COMMANDS_MODE:
        try:
            from ictbot.notify.tg_confirm import TGConfirmService

            _tg_confirm = TGConfirmService(
                token=TELEGRAM_TOKEN,
                operator_user_id=TG_OPERATOR_USER_ID,
                confirm_timeout_s=TG_CONFIRM_TIMEOUT_S,
                enable_commands=TG_COMMANDS_MODE,
            )
            # When confirm-mode is off but commands-mode is on, on_confirm
            # is never called (no buttons sent), so a no-op is safe.
            _tg_confirm.start(on_confirm=lambda r: router.route(r))
            log.info(
                "TG service on: confirm=%s commands=%s operator=%s timeout=%ds",
                TG_CONFIRM_MODE,
                TG_COMMANDS_MODE,
                TG_OPERATOR_USER_ID,
                TG_CONFIRM_TIMEOUT_S,
            )
        except Exception as exc:
            # Failing to start the service is fatal when the operator
            # asked for it — better to refuse to boot than to silently
            # flip back to default behaviour.
            log.error("TG service startup failed: %s", exc)
            raise

    if TG_HEARTBEAT_EVERY_N_CYCLES > 0:
        log.info(
            "tg heartbeat: per-pair card pack will fire every %d cycle(s)",
            TG_HEARTBEAT_EVERY_N_CYCLES,
        )

    cycle_counter = 0

    while True:
        try:
            # J10 (audit gap #18): stamp the heartbeat at the TOP of every
            # iteration so a hung evaluate loop visibly stops touching the
            # file. A supervisor checks `heartbeat.is_stale(120)` and
            # alerts on staleness.
            heartbeat.beat()

            # Honour the kill switch at the top of each scan — any single
            # iteration may flip it.
            engaged = kill_switch.is_engaged()
            metrics.kill_switch_engaged.set(1 if engaged else 0)
            if engaged:
                log.warning("kill switch engaged — pausing evaluation")
                time.sleep(30)
                continue

            # Phase D: TG operator pause. Auto-expires so we resume on
            # the first tick after the timestamp passes.
            if pause.is_active():
                log.info("pause active (%ds remaining) — skipping cycle", pause.remaining_seconds())
                time.sleep(30)
                continue

            # Phase 9: warm the news-feed cache once per loop so all pairs
            # share one disk read instead of competing for it. Best-effort —
            # the per-pair gate already handles fetch failures safely.
            try:
                from ictbot.runtime import news as _news

                _news.refresh_news()
            except Exception as e:
                log.warning(f"news refresh failed (continuing): {e}")

            # Optional: standalone news-aware alert. Dedup'd, so even on a
            # 30s loop the same event won't spam. Gated by env var so it
            # stays off unless the operator opts in.
            from ictbot.settings import NEWS_ALERT_WINDOW_MIN

            if NEWS_ALERT_WINDOW_MIN > 0:
                try:
                    from ictbot.notify.news_alert import check_and_alert

                    fired = check_and_alert(window_min=NEWS_ALERT_WINDOW_MIN)
                    if fired:
                        log.info(f"news alert sent: {fired.country} {fired.title}")
                except Exception as e:
                    log.warning(f"news alert failed (continuing): {e}")

            # Collected so the optional TG heartbeat at end of cycle can
            # ship one card pack covering every pair without re-running
            # analyze_pair (which would double the Delta API spend).
            cycle_results: list[dict] = []

            for pair in PAIRS:
                r = _evaluate_with_metrics(pair)
                cycle_results.append(r)

                # Audit gap #8: dedup on the *closed* bar time. The bar
                # at iloc[-1] is still forming; only iloc[-2] is final.
                # If we've already evaluated that bar this scan cycle, skip.
                ltf_df = r.get("ltf_df") if not r["error"] else None
                if ltf_df is not None and len(ltf_df) >= 2:
                    closed_t = ltf_df.iloc[-2]["time"]
                    if _last_seen_bar.get(pair) == closed_t:
                        continue
                    _last_seen_bar[pair] = closed_t

                # Audit gap #2: settle paper TP/SL + reconcile live broker
                # against the latest CLOSED bar before any signal routing.
                # Frees the cap so the new signal isn't rejected against a
                # position that's already exited on the exchange.
                if not r["error"]:
                    _settle_broker_on_last_closed_bar(router, pair, r)

                if r["error"]:
                    log.warning(f"[{pair}] {r['error']}")
                    jlog.warning(
                        "evaluation_error",
                        extra={"pair": pair, "error": r["error"]},
                    )
                    continue

                msg = (
                    f"[{r['pair']:>18}] "
                    f"entry={r['entry']:<8} "
                    f"price={r['price']:<10} "
                    f"conf={r['confidence']}% "
                    f"htf={r['htf_bias']:<7} ltf={r['ltf_bias']:<7} "
                    f"poi={r['poi_tap']:<10} mss={r['ltf_mss']:<11} fvg={r['fvg']}"
                )
                if r["entry"] in ("BUY", "SELL"):
                    log.warning(f"SIGNAL  {msg}")
                    jlog.warning(
                        "signal_fired",
                        extra={
                            "pair": r["pair"],
                            "entry": r["entry"],
                            "price": r["price"],
                            "sl": r["sl"],
                            "tp": r["tp"],
                            "rr": r["rr"],
                            "confidence": r["confidence"],
                            "htf_bias": r["htf_bias"],
                        },
                    )
                    _route_signal(router, r)
                elif r["diagnostics"]["near_miss"]:
                    log.info(f"NEAR    {msg}  [blocker: {r['diagnostics']['blockers'][0]}]")
                    jlog.info(
                        "near_miss",
                        extra={
                            "pair": r["pair"],
                            "blocker": r["diagnostics"]["blockers"][0],
                            "closest_direction": r["diagnostics"]["closest_direction"],
                        },
                    )
                    _notify_near_miss(r)
                else:
                    log.info(msg)

            cycle_counter += 1
            log.info("--- SCAN COMPLETE (cycle %d) ---", cycle_counter)

            # TG heartbeat — best-effort. Errors here must NEVER break the
            # scan loop; analyze_pair already sent BUY/SELL alerts inline.
            #
            # Session gate: outside London/NY killzones, heartbeats are
            # suppressed UNLESS a pair's confidence cleared the bypass
            # threshold — then we send with an off-session disclaimer.
            if (
                TG_HEARTBEAT_EVERY_N_CYCLES > 0
                and cycle_counter % TG_HEARTBEAT_EVERY_N_CYCLES == 0
                and cycle_results
            ):
                try:
                    from ictbot.notify.signal_check import build_message
                    from ictbot.notify.telegram import send_telegram

                    # Max confidence across all evaluated pairs decides
                    # whether a single high-conviction setup overrides the
                    # gate for the whole card pack.
                    max_conf = max(
                        (
                            int(r.get("confidence", 0) or 0)
                            for r in cycle_results
                            if not r.get("error")
                        ),
                        default=0,
                    )
                    in_session = is_killzone_active()
                    send_ok, prefix = decide_notify(
                        in_session=in_session,
                        confidence=max_conf,
                        in_session_only=TG_IN_SESSION_ONLY,
                        min_confidence_bypass=TG_MIN_CONFIDENCE_BYPASS,
                    )
                    if not send_ok:
                        log.info(
                            "tg heartbeat suppressed (off-session, max_conf=%d < %d)",
                            max_conf,
                            TG_MIN_CONFIDENCE_BYPASS,
                        )
                    else:
                        msg = prefix + build_message(cycle_results, full=False)
                        ok = send_telegram(msg)
                        log.info(
                            "tg heartbeat sent=%s (cycle %d, in_session=%s, max_conf=%d)",
                            ok,
                            cycle_counter,
                            in_session,
                            max_conf,
                        )
                except Exception as exc:
                    log.warning("tg heartbeat failed (continuing): %s", exc)

            time.sleep(30)
        except Exception as e:
            log.exception(f"scan loop error: {e}")
            jlog.exception("scan_loop_error", extra={"error": str(e)})
            time.sleep(10)


if __name__ == "__main__":
    main()
