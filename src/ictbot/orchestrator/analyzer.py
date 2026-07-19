"""
Composition root for one ICT scan.

`evaluate_frames` is the pure, network-free entry point: it constructs
an `ICTProMaxStrategy` from the supplied parameters (falling back to
the module-level BIAS_ENGINE / POI_ENGINE / STRATEGY_MODE) and runs
the evaluation. `analyze_pair` adds I/O: fetch fresh data, send a
Telegram alert on a new BUY/SELL, and append to the journal.

The function-level globals (`BIAS_ENGINE`, `POI_ENGINE`, `STRATEGY_MODE`,
`get_data`, `send_telegram`) are kept for the convenience of tests that
monkeypatch them. New code should pass an explicit strategy or rely on
the unified Settings object.
"""

import pandas as pd

# Routed through ictbot.data.factory so EXCHANGE=delta|binance picks the
# adapter at runtime. Tests that need to override the venue can monkey-
# patch `factory.get_default_exchange` or call `factory.set_default_exchange`.
from ictbot.data.factory import get_data, get_default_exchange
from ictbot.notify.telegram import send_telegram
from ictbot.portfolio.journal import (  # noqa: F401  (append_signal re-exported; tests monkeypatch analyzer.append_signal)
    append_signal,
    settle_open_signals,
)
from ictbot.runtime.sessions import get_sessions
from ictbot.runtime.signal_memory import load_last_signal, save_last_signal
from ictbot.settings import (
    BIAS_ENGINE,
    BIAS_TIMEFRAME,
    ENTRY_TIMEFRAME,
    FIB_FILTER,
    FIB_LOOKBACK_BARS,
    HTF_TIMEFRAME,
    MSS_TIMEFRAME,
    NEWS_BLACKOUT_COUNTRIES,
    NEWS_BLACKOUT_IMPACTS,
    NEWS_BLACKOUT_MINUTES,
    POI_ENGINE,
    POI_FRAME,
    POI_TIMEFRAME,
    REQUIRE_BIAS_ALIGNMENT,
    REQUIRE_FVG_AFTER_MSS,
    REQUIRE_MFVG_RETEST,
    SL_ANCHOR,
    STRATEGY_MODE,
    STRUCTURAL_TP1_RR,
    settings,
)
from ictbot.strategy.ict_pro_max import (
    MIN_BARS,
    ICTProMaxStrategy,
    _diagnose,
    _empty_result,
    _has_enough_data,
)

# Module-level singleton kept for backwards compatibility with tests that
# do `monkeypatch.setattr(analyzer._default_exchange, "tick_size", ...)`.
# Built lazily by the factory; venue is whatever `settings.exchange` says.
_default_exchange = get_default_exchange()

# Re-exports for backwards compatibility — tests + backtest engine import
# these from `ictbot.orchestrator.analyzer` directly.
__all__ = [
    "evaluate_frames",
    "analyze_pair",
    "ICTProMaxStrategy",
    "MIN_BARS",
    "_diagnose",
    "_empty_result",
    "_has_enough_data",
    "BIAS_ENGINE",
    "POI_ENGINE",
    "STRATEGY_MODE",
    "HTF_TIMEFRAME",
    "BIAS_TIMEFRAME",
    "POI_TIMEFRAME",
    "ENTRY_TIMEFRAME",
    "SL_ANCHOR",
    "STRUCTURAL_TP1_RR",
    "get_data",
    "send_telegram",
]


def evaluate_frames(
    htf_df: pd.DataFrame,
    bias_df: pd.DataFrame,
    poi_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    session: dict,
    pair: str = "TEST",
    *,
    poi_tolerance: float | None = None,
    sl_frac: float = 0.005,
    tp_frac: float = 0.015,
    sl_atr_mult: float | None = None,
    tp_atr_mult: float | None = None,
    require_fvg: bool = False,  # B3: flipped to align with §15 holding configs
    invert: bool | None = None,
    bias_engine: str | None = None,
    poi_engine: str | None = None,
    mss_mode: str = "swing",  # E2: ICT-canonical default
    mitigation_bars: int | None = None,
    tick_size: float | None = None,
    killzone_required: bool = False,
    skip_in_low_vol: bool = False,
    news_blackout_minutes: float = 0.0,
    news_blackout_countries: tuple[str, ...] = ("USD",),
    news_blackout_impacts: tuple[str, ...] = ("High",),
    delta_mode: str = "sign",
    relative_delta_threshold: float = 0.5,
    delta_window: int = 20,
    sl_anchor: str = "fixed",
    structural_tp1_rr: float = 2.0,
    mss_timeframe: str = "poi",
    require_fvg_after_mss: bool = True,
    require_mfvg_retest: bool = True,
    poi_frame: str = "htf_then_poi",
    fib_filter: float | None = None,
    fib_lookback_bars: int = 20,
    require_bias_alignment: bool = False,
) -> dict:
    """Pure evaluation — no I/O. Used by analyze_pair and the backtest.

    Tunable knobs:
      poi_tolerance — how close price must come to the POI to count as tapped.
      sl_frac / tp_frac — fixed-fraction stop and take-profit distances.
      sl_atr_mult / tp_atr_mult — when set, SL/TP = N × ATR(14) on 1m.
      require_fvg — when False, a micro FVG is not required for entry.
      invert — None means defer to STRATEGY_MODE; True forces fade; False forces follow.
      bias_engine / poi_engine — override the module-level defaults (used by
        ictbot.engine.compare so it can swap engines without mutating globals).
    """
    # Resolve mode + engines from explicit args, else fall back to module
    # globals so legacy tests that patch analyzer.BIAS_ENGINE still work.
    if invert is None:
        mode = "fade" if STRATEGY_MODE == "fade" else "follow"
    else:
        mode = "fade" if invert else "follow"

    eff_bias = bias_engine if bias_engine is not None else BIAS_ENGINE
    eff_poi = poi_engine if poi_engine is not None else POI_ENGINE

    # J15 (audit gap #23): cache the Strategy instance keyed by its knobs.
    # Backtest constructs ~50k of these per run; the constructor allocates
    # several small dicts each time. The strategy itself is stateless, so
    # one instance per knob-tuple is safe to reuse across calls.
    strat = _get_or_build_strategy(
        bias_engine=eff_bias,
        poi_engine=eff_poi,
        strategy_mode=mode,
        poi_tolerance=poi_tolerance,
        sl_frac=sl_frac,
        tp_frac=tp_frac,
        sl_atr_mult=sl_atr_mult,
        tp_atr_mult=tp_atr_mult,
        require_fvg=require_fvg,
        mss_mode=mss_mode,
        mitigation_bars=mitigation_bars,
        tick_size=tick_size,
        killzone_required=killzone_required,
        skip_in_low_vol=skip_in_low_vol,
        news_blackout_minutes=news_blackout_minutes,
        news_blackout_countries=news_blackout_countries,
        news_blackout_impacts=news_blackout_impacts,
        delta_mode=delta_mode,
        relative_delta_threshold=relative_delta_threshold,
        delta_window=delta_window,
        sl_anchor=sl_anchor,
        structural_tp1_rr=structural_tp1_rr,
        mss_timeframe=mss_timeframe,
        require_fvg_after_mss=require_fvg_after_mss,
        require_mfvg_retest=require_mfvg_retest,
        poi_frame=poi_frame,
        fib_filter=fib_filter,
        fib_lookback_bars=fib_lookback_bars,
        require_bias_alignment=require_bias_alignment,
    )
    return strat.evaluate(htf_df, bias_df, poi_df, entry_df, session, pair=pair)


# Module-level cache for J15. Bounded by the cardinality of distinct
# knob tuples a single process will see (small — typically 1 per run).
_STRAT_CACHE: dict[tuple, "ICTProMaxStrategy"] = {}


def _get_or_build_strategy(**kw) -> "ICTProMaxStrategy":
    """Return a cached ICTProMaxStrategy for the given knobs, building one
    on miss. The cache key is the sorted-kwargs tuple; all knobs are
    hashable scalars (str / float / int / bool / None)."""
    key = tuple(sorted(kw.items()))
    cached = _STRAT_CACHE.get(key)
    if cached is not None:
        return cached
    strat = ICTProMaxStrategy(**kw)
    _STRAT_CACHE[key] = strat
    return strat


def _reset_strategy_cache() -> None:
    """Testing hook — clears the J15 cache between scenarios."""
    _STRAT_CACHE.clear()


def analyze_pair(
    selected_pair: str,
    notify: bool = True,
    invert: bool | None = None,
    *,
    mss_mode: str | None = None,
    mss_timeframe: str | None = None,
    require_fvg_after_mss: bool | None = None,
    require_mfvg_retest: bool | None = None,
) -> dict:
    """Fetch fresh data for `selected_pair` and run the full evaluation.

    `invert=None` (default) means defer to STRATEGY_MODE.
    `mss_mode` overrides the default (E2: "swing"); tests use this to
    opt into "simple" without flipping a global.
    `mss_timeframe` overrides Phase-B canonical default ("poi") so
    legacy tests can opt into "entry" without touching settings.
    `require_fvg_after_mss` overrides Phase-C canonical default (True)
    so legacy fixtures whose FVG and MSS share a bar can opt out.
    """
    session = get_sessions()
    try:
        htf_df = get_data(selected_pair, HTF_TIMEFRAME, 300)
        bias_df = get_data(selected_pair, BIAS_TIMEFRAME, 300)
        poi_df = get_data(selected_pair, POI_TIMEFRAME, 300)
        entry_df = get_data(selected_pair, ENTRY_TIMEFRAME, 300)
    except Exception as e:
        return _empty_result(selected_pair, session, error=f"fetch failed: {e}")

    # E1 (ROADMAP §E1): auto-discover tick size from the exchange so SL/TP
    # round to the correct precision (XRP wants 4 decimals, BTC wants 0.5).
    auto_tick = None
    try:
        auto_tick = _default_exchange.tick_size(selected_pair)
    except Exception:
        auto_tick = None

    eff_mss_mode = mss_mode if mss_mode is not None else "swing"
    result = evaluate_frames(
        htf_df,
        bias_df,
        poi_df,
        entry_df,
        session,
        pair=selected_pair,
        invert=invert,
        tick_size=auto_tick,
        mss_mode=eff_mss_mode,
        # Fix 9.A (plan: Phase 9 per-token completeness): read per-pair
        # SL/TP overrides with fallback to global. Pairs differ in
        # volatility regime — a single global sl_frac/tp_frac was the
        # dominant edge leak surfaced by the 5-token audit.
        sl_frac=settings.get_sl_frac(selected_pair),
        tp_frac=settings.get_tp_frac(selected_pair),
        # Fix 12.A (plan: Phase 12 per-pair POI tolerance): the Phase 9.A
        # WFO showed winning POI tolerance varying 0.0015 → 0.01 across
        # pairs (a 7× spread). Single-global POI_TAP_TOLERANCE was the
        # next edge leak after SL/TP. Same helper shape as Fix 9.A.
        poi_tolerance=settings.get_poi_tap_tolerance(selected_pair),
        # Forward live-config gates from settings so the env var actually
        # affects production runs (offline callers default to 0 = off).
        news_blackout_minutes=NEWS_BLACKOUT_MINUTES,
        news_blackout_countries=NEWS_BLACKOUT_COUNTRIES,
        news_blackout_impacts=NEWS_BLACKOUT_IMPACTS,
        # Box 7/8 of the canonical flow. Default settings = "fixed" so
        # this is a no-op until SL_ANCHOR=structural is set in the env.
        sl_anchor=SL_ANCHOR,
        structural_tp1_rr=STRUCTURAL_TP1_RR,
        # Box 3 of the canonical flow. Default "poi" = 3m MSS;
        # tests can opt into "entry" via the analyze_pair kwarg.
        mss_timeframe=mss_timeframe if mss_timeframe is not None else MSS_TIMEFRAME,
        # Box 4 of the canonical flow. Default True = spec; tests can
        # opt out via the analyze_pair kwarg without touching settings.
        require_fvg_after_mss=(
            require_fvg_after_mss if require_fvg_after_mss is not None else REQUIRE_FVG_AFTER_MSS
        ),
        # Box 5: MFVG retest gate. Default True = spec.
        require_mfvg_retest=(
            require_mfvg_retest if require_mfvg_retest is not None else REQUIRE_MFVG_RETEST
        ),
        # Box 2: which frame the POI lives on. Default = htf_then_poi.
        poi_frame=POI_FRAME,
        # Premium/discount filter on the OB. Default None = off; opt-in
        # via FIB_FILTER env var after empirical validation through
        # engine.wfo (see docs/findings_artifact_diff.md).
        fib_filter=FIB_FILTER,
        fib_lookback_bars=FIB_LOOKBACK_BARS,
        # Phase E — HTF/LTF bias alignment. Default True in settings.py
        # because production runs need it to block "short into bullish
        # LTF" disasters. Tests that need pre-Phase-E behaviour can pass
        # require_bias_alignment=False directly to evaluate_frames.
        require_bias_alignment=REQUIRE_BIAS_ALIGNMENT,
    )

    # --- Settle previously OPEN signals using the latest *closed* candle ---
    # Audit gap #7: ccxt returns the still-forming current bar as iloc[-1].
    # Settling against its running high/low yields phantom WIN/LOSS on
    # intra-bar wicks that the real bar may never realise. iloc[-2] is the
    # last bar whose OHLC is final.
    if not result["error"] and len(entry_df) >= 2:
        last_closed = entry_df.iloc[-2]
        settle_open_signals(
            {
                selected_pair: {
                    "high": float(last_closed["high"]),
                    "low": float(last_closed["low"]),
                }
            }
        )

    # --- Telegram + journal (dedup on pair+direction) ---
    if notify and result["entry"] in ("BUY", "SELL"):
        signal_key = f"{selected_pair}_{result['entry']}"
        last = load_last_signal()
        if last.get("signal") != signal_key:
            # Real BUY/SELL fires always have confidence=100 (every
            # canonical gate passed), so the session gate's bypass
            # threshold (also 100 by default) always lets them through.
            # The only effect is prepending the off-session disclaimer
            # when the killzone is closed.
            from ictbot.runtime.session_gate import decide_notify
            from ictbot.runtime.sessions import is_killzone_active
            from ictbot.settings import TG_IN_SESSION_ONLY, TG_MIN_CONFIDENCE_BYPASS

            in_session = is_killzone_active(session)
            send_ok, prefix = decide_notify(
                in_session=in_session,
                confidence=int(result.get("confidence", 0) or 0),
                in_session_only=TG_IN_SESSION_ONLY,
                min_confidence_bypass=TG_MIN_CONFIDENCE_BYPASS,
            )
            if send_ok:
                msg = prefix + _format_telegram(
                    pair=selected_pair,
                    entry=result["entry"],
                    price=result["price"],
                    sl=result["sl"],
                    tp=result["tp"],
                    rr=result["rr"],
                    delta=result["delta"],
                    confidence=result["confidence"],
                    htf_bias=result["htf_bias"],
                    ltf_bias=result["ltf_bias"],
                    poi_tap=result["poi_tap"],
                    ltf_mss=result["ltf_mss"],
                    micro_fvg=result["micro_fvg"],
                    session=session["active_session"],
                )
                send_telegram(msg)
                save_last_signal({"signal": signal_key})
                # Journal writes now live entirely on the router/broker
                # path (router._journal_placed for fills, _journal_rejected
                # for cap rejections). The analyzer used to write here too
                # but that produced phantom OPEN rows that settle_open_-
                # signals would mis-settle as wins/losses against the next
                # bar's high/low — see 2026-06-05 incident in the plan
                # file. TG notification is the analyzer's only side-effect.

    return result


def _format_telegram(**kw) -> str:
    return (
        "ICT AI BOT PRO MAX\n\n"
        f"PAIR : {kw['pair']}\n"
        f"ENTRY : {kw['entry']}\n"
        f"PRICE : {kw['price']}\n"
        f"SL : {kw['sl']}\n"
        f"TP : {kw['tp']}\n"
        f"RR : 1:{kw['rr']}\n"
        f"DELTA : {kw['delta']}\n"
        f"CONFIDENCE : {kw['confidence']}%\n"
        f"HTF BIAS : {kw['htf_bias']}\n"
        f"LTF BIAS : {kw['ltf_bias']}\n"
        f"POI : {kw['poi_tap']}\n"
        f"MSS : {kw['ltf_mss']}\n"
        f"FVG : {kw['micro_fvg']}\n"
        f"SESSION : {kw['session']}\n"
    )
