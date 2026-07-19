"""
ICTProMaxStrategy — the current ICT-style scalp setup as a Strategy.

This is a straight lift of the logic that used to live in
`ictbot.orchestrator.analyzer.evaluate_frames`, refactored so the
bias engine, POI engine, mode and risk parameters are passed at
construction time instead of read from module globals.

`evaluate_frames` is still exposed (as a thin wrapper) so existing
callers (backtest, analyze_pair, tests) keep working.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from ictbot.indicators.atr import get_atr
from ictbot.indicators.bias_slope import get_slope_bias
from ictbot.indicators.bias_sma import (
    get_htf_bias as sma_htf_bias,
)
from ictbot.indicators.bias_sma import (
    get_ltf_bias as sma_ltf_bias,
)
from ictbot.indicators.delta import get_delta, get_relative_delta
from ictbot.indicators.fvg import get_micro_fvg_info, get_micro_fvg_range
from ictbot.indicators.liquidity import get_next_liquidity_level
from ictbot.indicators.mfvg_retest import has_mfvg_retest
from ictbot.indicators.mitigation import is_mitigated
from ictbot.indicators.mss import get_ltf_mss, get_ltf_mss_time
from ictbot.indicators.poi_min_max import get_ltf_poi, get_poi_tap
from ictbot.indicators.poi_order_block import get_ob_poi
from ictbot.indicators.regime import atr_percentile_regime
from ictbot.indicators.risk import calculate_rr
from ictbot.indicators.structure import get_swing_bias
from ictbot.indicators.tick import round_to_tick
from ictbot.strategy.base import Strategy

# Minimum bars we need per timeframe to compute anything sensible.
MIN_BARS = {"htf": 50, "bias": 20, "poi": 20, "entry": 5}


BiasEngine = Literal["sma", "swing", "slope"]
PoiEngine = Literal["min_max", "order_block"]
StrategyMode = Literal["follow", "fade"]
DeltaMode = Literal["sign", "relative"]
SLAnchor = Literal["fixed", "structural"]
# Box 3 of the canonical flow: MSS confirmation belongs on the 3m POI
# frame, not the 1m entry frame. "poi" routes there; "entry" preserves
# the pre-Phase-B behaviour for legacy backtests.
MSSTimeframe = Literal["entry", "poi"]
# Box 2 of the canonical flow: which frame the POI is computed on.
# "htf"          — compute on htf_df (4h). Strictest spec match.
# "htf_then_poi" — try HTF first; on WAITING, fall back to poi_df (3m).
#                  Pragmatic default — catches macro POIs when price is
#                  there AND intraday POIs when it's not.
# "poi"          — legacy: compute on poi_df only.
POIFrame = Literal["poi", "htf", "htf_then_poi"]


class ICTProMaxStrategy(Strategy):
    """ICT-style scalp: HTF bias → POI tap → MSS → micro-FVG → delta → entry."""

    def __init__(
        self,
        # Phase A: defaults aligned to the canonical ICT flow. Legacy
        # callers that need "fade" / "sma" / "min_max" must pass them
        # explicitly. The CANONICAL_FLOW=off env var rolls these back
        # at the settings level for production.
        bias_engine: BiasEngine = "swing",
        poi_engine: PoiEngine = "order_block",
        strategy_mode: StrategyMode = "follow",
        *,
        poi_tolerance: float | None = None,
        sl_frac: float = 0.005,
        tp_frac: float = 0.015,
        sl_atr_mult: float | None = None,
        tp_atr_mult: float | None = None,
        # B3 (ROADMAP §B3): default flipped True → False. The §15 holding
        # configs all had fvg=False; the FVG requirement was suppressing
        # signal count below the statistical floor at high RR.
        require_fvg: bool = False,
        tick_size: float | None = None,
        # E2 (ROADMAP §E2): default flipped "simple" → "swing". The 2-bar
        # "simple" rule fires on noise; the swing rule waits for a real
        # break of the protected swing. ICT-canonical behaviour.
        mss_mode: str = "swing",
        # Phase B (canonical Box 3): which frame MSS runs on. "poi"
        # = 3m POI frame (spec). "entry" = 1m entry frame (legacy).
        # Defaults to "poi" to match the canonical flow.
        mss_timeframe: MSSTimeframe = "poi",
        # Phase C (canonical Box 4): when True, the MFVG must form on a
        # bar with timestamp strictly later than the MSS confirmation
        # bar. Catches the "FVG-then-MSS" sequence the legacy code
        # accepted as valid. Default True = match the canonical flow.
        require_fvg_after_mss: bool = True,
        # Phase D (canonical Box 5): when True, a later bar's CLOSE must
        # fall inside the MFVG range before entry can fire. The MFVG
        # printing alone is not enough — ICT canon requires the retest
        # confirmation. Default True = spec. Inert when no MFVG range
        # is available (entry gate fails on missing FVG anyway).
        require_mfvg_retest: bool = True,
        # Phase F (canonical Box 2): which frame the POI is computed on.
        # See the POIFrame type alias for semantics. Default
        # "htf_then_poi" matches the user-confirmed pragmatic strategy.
        poi_frame: POIFrame = "htf_then_poi",
        mitigation_bars: int | None = None,
        killzone_required: bool = False,
        skip_in_low_vol: bool = False,
        # Phase 9 — news blackout. When > 0, query the ForexFactory feed
        # and refuse to fire within ±N minutes of any matching macro event.
        # Defaults are off (0) so existing behaviour is unchanged unless
        # opted-in via settings.NEWS_BLACKOUT_MINUTES.
        news_blackout_minutes: float = 0.0,
        news_blackout_countries: tuple[str, ...] = ("USD",),
        news_blackout_impacts: tuple[str, ...] = ("High",),
        delta_mode: DeltaMode = "sign",
        relative_delta_threshold: float = 0.5,
        # Audit gap #3: get_delta sums whatever DataFrame you pass it. In
        # live the entry_df is 300 bars (~5h). In backtest, entry_window
        # grows toward 50k bars (~34 days). The "delta > 0" gate measures
        # something completely different in the two paths — at 50k bars,
        # cumulative signed volume is dominated by historical accumulation
        # and the gate becomes a no-op. Fixed-tail windowing makes the
        # gate honest in both paths. Default 20 matches get_relative_delta.
        delta_window: int = 20,
        # Box 7/8 of the canonical flow: when "structural", anchor SL to
        # the MFVG edge and TP1 to 1:N RR off that real R distance, with
        # TP2 = next unbroken liquidity level. "fixed" keeps the legacy
        # sl_frac/tp_frac (or ATR) behaviour bit-for-bit identical.
        sl_anchor: SLAnchor = "fixed",
        structural_tp1_rr: float = 2.0,
        # Premium/discount filter on the OB (docs/findings_artifact_diff.md).
        # When set (e.g. 0.5), OBs whose midpoint sits in the wrong half
        # of the recent `fib_lookback_bars`-bar swing leg are skipped:
        # BULLISH wants discount (below 0.5), BEARISH wants premium
        # (above 0.5). None = off (legacy). Only applied when the POI
        # engine is "order_block"; the min/max engine has no comparable
        # filter and silently ignores it.
        fib_filter: float | None = None,
        fib_lookback_bars: int = 20,
        # Phase E — HTF/LTF bias alignment. When True, the entry gate
        # additionally requires `htf_bias == ltf_bias` before firing.
        # Default False at the class boundary so synthetic tests don't
        # need to align both frames; the analyzer passes True via
        # settings.REQUIRE_BIAS_ALIGNMENT to enforce it in production.
        require_bias_alignment: bool = False,
    ) -> None:
        self.bias_engine = bias_engine
        self.poi_engine = poi_engine
        self.strategy_mode = strategy_mode
        self.poi_tolerance = poi_tolerance
        self.sl_frac = sl_frac
        self.tp_frac = tp_frac
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.require_fvg = require_fvg
        # Phase 6 — optional correctness knobs.
        # tick_size:        None ⇒ legacy round(price, 2); set per market to
        #                   stop low-priced assets like XRP from suffering
        #                   the friction-vs-tick distortion documented in
        #                   docs/findings.md.
        # mss_mode:         "simple" (2-bar) or "swing" (real ICT break).
        # mitigation_bars:  None ⇒ no retirement (legacy). If set, a POI
        #                   that was tagged more than N bars ago is treated
        #                   as spent and no longer counts as "tapped".
        self.tick_size = tick_size
        self.mss_mode = mss_mode
        self.mss_timeframe = mss_timeframe
        self.require_fvg_after_mss = require_fvg_after_mss
        self.require_mfvg_retest = require_mfvg_retest
        self.poi_frame = poi_frame
        self.mitigation_bars = mitigation_bars
        # Phase 7 — environmental gates.
        # killzone_required: when True, suppress entries outside London/NY hours.
        # skip_in_low_vol:   when True, suppress entries while ATR percentile
        #                    on the 1m frame is in the bottom 30% of the last
        #                    200 bars. (HIGH_VOL is always allowed — that's
        #                    when ICT setups tend to work.)
        self.killzone_required = killzone_required
        self.skip_in_low_vol = skip_in_low_vol
        self.news_blackout_minutes = max(0.0, float(news_blackout_minutes))
        self.news_blackout_countries = tuple(news_blackout_countries) or ("USD",)
        self.news_blackout_impacts = tuple(news_blackout_impacts) or ("High",)
        # B3 (ROADMAP §B3): "sign" = legacy boolean delta > 0; "relative"
        # = delta normalised to median |delta| over the trailing window,
        # gated by `relative_delta_threshold`. Default stays "sign" for
        # backwards compat — opt-in via constructor / future flag.
        self.delta_mode = delta_mode
        self.relative_delta_threshold = relative_delta_threshold
        self.delta_window = max(1, int(delta_window))
        self.sl_anchor = sl_anchor
        # Guard against 0/negative RR — would invert TP relative to entry.
        self.structural_tp1_rr = max(0.1, float(structural_tp1_rr))
        # Premium/discount knob — see constructor docstring. None = off,
        # any float in (0, 1) sets the discount/premium split.
        self.fib_filter = fib_filter
        self.fib_lookback_bars = max(1, int(fib_lookback_bars))
        self.require_bias_alignment = bool(require_bias_alignment)

    # ---- bias engine selector --------------------------------------------
    def _get_htf_bias(self, df: pd.DataFrame) -> str:
        if self.bias_engine == "swing":
            return get_swing_bias(df)
        if self.bias_engine == "slope":
            return get_slope_bias(df)
        return sma_htf_bias(df)

    def _get_ltf_bias(self, df: pd.DataFrame) -> str:
        if self.bias_engine == "swing":
            return get_swing_bias(df)
        if self.bias_engine == "slope":
            return get_slope_bias(df)
        return sma_ltf_bias(df)

    # ---- entrypoint ------------------------------------------------------
    def evaluate(
        self,
        htf_df: pd.DataFrame,
        bias_df: pd.DataFrame,
        poi_df: pd.DataFrame,
        entry_df: pd.DataFrame,
        session: dict,
        pair: str = "TEST",
    ) -> dict:
        err = _has_enough_data(htf_df, bias_df, poi_df, entry_df)
        if err:
            return _empty_result(pair, session, error=err)

        current_price = float(entry_df["close"].iloc[-1])

        htf_bias = self._get_htf_bias(htf_df)
        ltf_bias = self._get_ltf_bias(bias_df)  # diagnostic-only

        # Box 2 of the canonical flow: which frame does the POI live on?
        # Default `htf_then_poi` tries HTF (4h) first — macro liquidity
        # pools matter most when price is near them — and falls back to
        # the 3m POI frame when HTF says WAITING so intraday setups
        # still surface. `htf` strictly uses 4h; `poi` is the pre-Phase-F
        # behaviour.
        def _poi_on(frame_df):
            if self.poi_engine == "order_block":
                # Forward fib_filter kwargs only when enabled so tests
                # that monkey-patch get_ob_poi with the legacy signature
                # keep working when the filter is off (the default).
                ob_kwargs = {
                    "mitigation_bars": self.mitigation_bars,
                    "tick_size": self.tick_size,
                }
                if self.fib_filter is not None:
                    ob_kwargs["fib_filter"] = self.fib_filter
                    ob_kwargs["fib_lookback_bars"] = self.fib_lookback_bars
                level = get_ob_poi(frame_df, htf_bias, **ob_kwargs)
            else:
                level = get_ltf_poi(frame_df, htf_bias, tick_size=self.tick_size)
            tap = get_poi_tap(frame_df, level, tolerance_frac=self.poi_tolerance)
            if tap == "POI TAPPED" and self.mitigation_bars:
                side = "demand" if htf_bias == "BULLISH" else "supply"
                if is_mitigated(frame_df, level, side=side, retire_bars=self.mitigation_bars):
                    tap = "WAITING"
            return level, tap

        if self.poi_frame == "htf":
            ltf_poi, poi_tap = _poi_on(htf_df)
        elif self.poi_frame == "htf_then_poi":
            ltf_poi, poi_tap = _poi_on(htf_df)
            if poi_tap != "POI TAPPED":
                # HTF said no — try the 3m frame so intraday POIs aren't
                # silently dropped while macro is waiting.
                ltf_poi, poi_tap = _poi_on(poi_df)
        else:  # "poi" — legacy
            ltf_poi, poi_tap = _poi_on(poi_df)

        # Box 3 of the canonical flow: MSS confirmation on the 3m POI
        # frame, not 1m entry. The MSS-on-1m default fires on noise
        # (a single bar break) far more often than on a real LTF
        # structural shift; the spec wants the slower 3m signal.
        mss_frame = poi_df if self.mss_timeframe == "poi" else entry_df
        ltf_mss = get_ltf_mss(mss_frame, htf_bias, mode=self.mss_mode)

        # Box 4 of the canonical flow: the MFVG must form strictly after
        # the MSS bar. When `require_fvg_after_mss` is on AND MSS has
        # actually confirmed, we look up the MSS bar's timestamp and
        # pass it to the FVG search as a floor. Without MSS confirmation
        # the gate is a no-op (no timestamp to compare against), so an
        # FVG-only setup still surfaces in diagnostics but the entry
        # gate fails on the missing MSS anyway.
        min_fvg_time = None
        if self.require_fvg_after_mss and ltf_mss in ("BULLISH MSS", "BEARISH MSS"):
            min_fvg_time = get_ltf_mss_time(mss_frame, htf_bias, mode=self.mss_mode)

        # E3 (ROADMAP §E3): pass mitigation_bars into FVG too — once a
        # gap has been filled within mitigation_bars of formation, it's
        # no longer a valid imbalance.
        #
        # Phase D: pull the FVG metadata (range + formation_time) so
        # the retest check has a reference for "strictly after". Keeps
        # one scan instead of two — label, range, retest all share the
        # same chosen gap.
        fvg_info = get_micro_fvg_info(
            entry_df,
            htf_bias,
            mitigation_bars=self.mitigation_bars,
            min_formation_time=min_fvg_time,
        )
        if fvg_info is None:
            micro_fvg = "NO FVG"
        else:
            micro_fvg = "BULLISH FVG" if htf_bias == "BULLISH" else "BEARISH FVG"

        # Box 5 of the canonical flow: price must close back inside the
        # MFVG range after it formed. The FVG itself isn't enough — ICT
        # canon requires the retest before entry. Inert when there's no
        # FVG to retest (the entry gate already fails on missing FVG).
        mfvg_retested = True
        if self.require_mfvg_retest and fvg_info is not None:
            mfvg_retested = has_mfvg_retest(
                entry_df,
                fvg_low=fvg_info["low"],
                fvg_high=fvg_info["high"],
                formation_time=fvg_info["formation_time"],
            )
        # Audit gap #3: explicit window so live and backtest measure the
        # same quantity.
        delta_df = entry_df.tail(self.delta_window)
        delta = get_delta(delta_df)
        # B3 relative-delta: compute alongside raw delta so diagnostics
        # always have both. Decision uses whichever matches delta_mode.
        rel_delta = get_relative_delta(entry_df) if self.delta_mode == "relative" else 0.0
        atr_1m = get_atr(entry_df, period=14)

        thresh = self.relative_delta_threshold
        if self.delta_mode == "relative":
            delta_buy = rel_delta > thresh
            delta_sell = rel_delta < -thresh
        else:
            delta_buy = delta > 0
            delta_sell = delta < 0

        # ---- confidence (4 weighted bits, max 100) ----------------------
        # Bug fixed: the legacy `"MSS" in ltf_mss` and `"FVG" in micro_fvg`
        # substring checks award the bit even on "NO MSS" / "NO FVG"
        # because the literal substring "MSS" appears inside "NO MSS".
        # That false-positive let off-session heartbeats bypass the
        # confidence gate at 100% even when MSS was missing.
        confidence = 0
        if poi_tap == "POI TAPPED":
            confidence += 25
        if ltf_mss in ("BULLISH MSS", "BEARISH MSS"):
            confidence += 25
        if (not self.require_fvg) or micro_fvg in ("BULLISH FVG", "BEARISH FVG"):
            confidence += 25
        bias_aligned_delta = (htf_bias == "BULLISH" and delta_buy) or (
            htf_bias == "BEARISH" and delta_sell
        )
        if bias_aligned_delta:
            confidence += 25

        # ---- environmental gates (Phase 7) ------------------------------
        gate_blocked: str | None = None
        if self.killzone_required and not session.get("killzone_active", False):
            gate_blocked = "outside killzone (London/NY closed)"

        regime: str | None = None
        if self.skip_in_low_vol:
            regime = atr_percentile_regime(entry_df)
            if regime == "LOW_VOL":
                gate_blocked = gate_blocked or "regime is LOW_VOL"

        # News blackout (Phase 9). Only queries the feed when the gate is
        # actually enabled — keeps offline tests / backtests fast by default.
        # If the FF feed is unreachable AND no cache exists, fail SAFE:
        # treat as blocked rather than letting a live trade through.
        news_event: dict | None = None
        if self.news_blackout_minutes > 0:
            try:
                from ictbot.runtime import news as _news  # lazy import (network)

                hit = _news.is_blackout(
                    self.news_blackout_minutes,
                    country=self.news_blackout_countries,
                    impact=self.news_blackout_impacts,
                )
                if hit is not None:
                    eta_min = (hit.ts - _news._utcnow()).total_seconds() / 60.0
                    gate_blocked = gate_blocked or (
                        f"news blackout: {hit.title} "
                        f"({eta_min:+.0f} min, {hit.country} {hit.impact})"
                    )
                    news_event = {
                        "title": hit.title,
                        "country": hit.country,
                        "impact": hit.impact,
                        "eta_minutes": eta_min,
                    }
            except Exception as e:
                gate_blocked = gate_blocked or f"news feed unavailable: {e}"

        # ---- entry decision ---------------------------------------------
        entry, sl, tp = "NO ENTRY", 0.0, 0.0
        fvg_ok_bull = (micro_fvg == "BULLISH FVG") if self.require_fvg else True
        fvg_ok_bear = (micro_fvg == "BEARISH FVG") if self.require_fvg else True

        # Phase E — dual-timeframe bias confirmation. The HTF call sets
        # the macro direction; LTF agreement says short-term momentum is
        # on the same side. Without this, the strategy was shorting into
        # bullish 15m rallies and getting stopped 94% of the time.
        bias_aligned = (not self.require_bias_alignment) or (htf_bias == ltf_bias)

        bullish_setup = gate_blocked is None and (
            htf_bias == "BULLISH"
            and bias_aligned
            and poi_tap == "POI TAPPED"
            and ltf_mss == "BULLISH MSS"
            and fvg_ok_bull
            and delta_buy
            and mfvg_retested  # Box 5: only fire on confirmed retest
        )
        bearish_setup = gate_blocked is None and (
            htf_bias == "BEARISH"
            and bias_aligned
            and poi_tap == "POI TAPPED"
            and ltf_mss == "BEARISH MSS"
            and fvg_ok_bear
            and delta_sell
            and mfvg_retested
        )

        use_atr = self.sl_atr_mult is not None and self.tp_atr_mult is not None and atr_1m > 0

        rt = lambda p: round_to_tick(p, self.tick_size)
        if bullish_setup:
            entry = "BUY"
            if use_atr:
                sl = rt(current_price - self.sl_atr_mult * atr_1m)
                tp = rt(current_price + self.tp_atr_mult * atr_1m)
            else:
                sl = rt(current_price * (1 - self.sl_frac))
                tp = rt(current_price * (1 + self.tp_frac))
        elif bearish_setup:
            entry = "SELL"
            if use_atr:
                sl = rt(current_price + self.sl_atr_mult * atr_1m)
                tp = rt(current_price - self.tp_atr_mult * atr_1m)
            else:
                sl = rt(current_price * (1 + self.sl_frac))
                tp = rt(current_price * (1 - self.tp_frac))

        # ---- fade-mode flip ---------------------------------------------
        # Done BEFORE structural anchoring so the structural block sees
        # the post-flip `entry` and can look up the FVG / liquidity in
        # the side the broker actually receives.
        if self.strategy_mode == "fade" and entry in ("BUY", "SELL"):
            if entry == "BUY":
                entry = "SELL"
                sl, tp = (
                    rt(current_price + (current_price - sl)),
                    rt(current_price - (tp - current_price)),
                )
            else:
                entry = "BUY"
                sl, tp = (
                    rt(current_price - (sl - current_price)),
                    rt(current_price + (current_price - tp)),
                )

        # ---- structural SL / TP1 / TP2 (Box 7/8 of canonical flow) -----
        # Look up the FVG in the TRADED direction (post any fade flip).
        # In follow mode that equals htf_bias; in fade mode it's the
        # opposite, so we DON'T pass min_fvg_time (the MSS time was
        # against bias and is meaningless for the opposite-direction
        # gap). TP2 ships as 0.0 outside this branch so the card omits
        # the row.
        tp2 = 0.0
        if self.sl_anchor == "structural" and entry in ("BUY", "SELL"):
            traded_bias = "BULLISH" if entry == "BUY" else "BEARISH"
            # min_fvg_time only applies when the FVG we're looking up
            # is in the bias direction (follow mode). In fade mode the
            # MSS confirmed against the trade, so the post-MSS gate
            # would reject every legitimate fade FVG.
            fvg_time_floor = min_fvg_time if traded_bias == htf_bias else None
            fvg_range = get_micro_fvg_range(
                entry_df,
                traded_bias,
                mitigation_bars=self.mitigation_bars,
                min_formation_time=fvg_time_floor,
            )
            if fvg_range is not None:
                gap_low, gap_high = fvg_range
                if entry == "BUY":
                    structural_sl = rt(gap_low)
                    risk_R = current_price - structural_sl
                    # Skip the override when math went negative (rare —
                    # current_price below gap_low means price already
                    # invalidated the setup). Fall back to whatever SL/TP
                    # the fixed/ATR path (or fade flip) wrote above.
                    if risk_R > 0:
                        sl = structural_sl
                        tp = rt(current_price + self.structural_tp1_rr * risk_R)
                else:  # SELL
                    structural_sl = rt(gap_high)
                    risk_R = structural_sl - current_price
                    if risk_R > 0:
                        sl = structural_sl
                        tp = rt(current_price - self.structural_tp1_rr * risk_R)
            # TP2 = next unbroken liquidity in trade direction. Try 3m
            # (poi_df) first for intraday targets; fall through to HTF
            # for macro liquidity when intraday yields nothing.
            liq = get_next_liquidity_level(poi_df, entry, current_price)
            if liq is None:
                liq = get_next_liquidity_level(htf_df, entry, current_price)
            if liq is not None:
                tp2 = rt(liq)

        rr = calculate_rr(current_price, sl, tp) if entry != "NO ENTRY" else 0.0
        diag = _diagnose(
            htf_bias,
            poi_tap,
            ltf_mss,
            micro_fvg,
            delta,
            require_fvg=self.require_fvg,
            delta_mode=self.delta_mode,
            rel_delta=rel_delta,
            relative_delta_threshold=self.relative_delta_threshold,
            mfvg_retested=mfvg_retested if self.require_mfvg_retest else None,
            ltf_bias=ltf_bias,
            require_bias_alignment=self.require_bias_alignment,
        )

        # Proposed SL/TP for the TG card: shows what the bracket would
        # look like IF the bot fired right now. Reuses the same
        # sl_frac/tp_frac or ATR multipliers as a real fire, so the
        # projection is consistent with how the strategy actually sizes
        # risk. `proposed_direction` is the POST-fade side the broker
        # would receive — in fade mode the diagnostic's closest_direction
        # is the pre-flip side and the actual broker side is its opposite.
        if entry in ("BUY", "SELL"):
            # Live fire — already post-fade from the block above.
            proposed_direction = entry
            proposed_sl, proposed_tp = sl, tp
        else:
            pre_flip = diag.get("closest_direction")
            proposed_sl, proposed_tp = _project_levels(
                current_price,
                pre_flip,
                strategy_mode=self.strategy_mode,
                sl_atr_mult=self.sl_atr_mult,
                tp_atr_mult=self.tp_atr_mult,
                sl_frac=self.sl_frac,
                tp_frac=self.tp_frac,
                atr_1m=atr_1m,
                rt=rt,
            )
            if self.strategy_mode == "fade" and pre_flip in ("BUY", "SELL"):
                proposed_direction = "SELL" if pre_flip == "BUY" else "BUY"
            else:
                proposed_direction = pre_flip or "BUY"
        proposed_rr = (
            calculate_rr(current_price, proposed_sl, proposed_tp)
            if proposed_sl > 0 and proposed_tp > 0
            else 0.0
        )

        return {
            # identity
            "pair": pair,
            "error": None,
            # prices
            "price": current_price,
            "last_close": current_price,
            # ICT stack
            "htf_bias": htf_bias,
            "ltf_bias": ltf_bias,
            "ltf_poi": ltf_poi,
            "poi_tap": poi_tap,
            "ltf_mss": ltf_mss,
            "fvg": micro_fvg,
            "micro_fvg": micro_fvg,
            "delta": delta,
            "relative_delta": rel_delta,
            "delta_mode": self.delta_mode,
            "atr_1m": atr_1m,
            # signal
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "tp2": tp2,  # next external liquidity (0.0 when none / legacy mode)
            "rr": rr,
            "confidence": confidence,
            # Projection — what the bracket WOULD look like if the bot
            # fired in the closest direction at the current bar. Equal to
            # sl/tp/rr when entry is BUY/SELL; non-zero on NO ENTRY too so
            # the TG card can always show a proposed bracket.
            "proposed_direction": proposed_direction,
            "proposed_sl": proposed_sl,
            "proposed_tp": proposed_tp,
            "proposed_rr": proposed_rr,
            # gates / regime (Phase 7) + news (Phase 9)
            "gate_blocked": gate_blocked,
            "regime": regime,
            "news_event": news_event,
            # diagnostics
            "diagnostics": diag,
            # session (flattened so UI can read data["tokyo_time"] etc.)
            "india_time": session["india_time"],
            "tokyo_time": session["tokyo_time"],
            "tokyo_status": session["tokyo_status"],
            "london_time": session["london_time"],
            "london_status": session["london_status"],
            "newyork_time": session["newyork_time"],
            "newyork_status": session["newyork_status"],
            "active_session": session["active_session"],
            # raw frames the UI may want to chart
            "ltf_df": entry_df,
            "poi_df": poi_df,
        }


# -- helpers ------------------------------------------------------------------


def _project_levels(
    price: float,
    direction: str | None,
    *,
    strategy_mode: str,
    sl_atr_mult: float | None,
    tp_atr_mult: float | None,
    sl_frac: float,
    tp_frac: float,
    atr_1m: float,
    rt,
) -> tuple[float, float]:
    """Project what SL/TP would be if the bot fired NOW in `direction`.

    Mirrors the live entry logic so the TG card's proposed bracket is a
    faithful preview, not an invented number. Returns (0.0, 0.0) when
    direction is unknown or price is missing.

    `strategy_mode == "fade"` applies the same long↔short flip the live
    path applies, so the projection matches what the broker would
    actually receive.
    """
    if not direction or price <= 0:
        return 0.0, 0.0

    use_atr = sl_atr_mult is not None and tp_atr_mult is not None and atr_1m > 0
    if direction == "BUY":
        if use_atr:
            sl = rt(price - sl_atr_mult * atr_1m)
            tp = rt(price + tp_atr_mult * atr_1m)
        else:
            sl = rt(price * (1 - sl_frac))
            tp = rt(price * (1 + tp_frac))
    else:  # SELL
        if use_atr:
            sl = rt(price + sl_atr_mult * atr_1m)
            tp = rt(price - tp_atr_mult * atr_1m)
        else:
            sl = rt(price * (1 + sl_frac))
            tp = rt(price * (1 - tp_frac))

    if strategy_mode == "fade":
        if direction == "BUY":
            sl, tp = rt(price + (price - sl)), rt(price - (tp - price))
        else:
            sl, tp = rt(price - (sl - price)), rt(price + (price - tp))

    return sl, tp


def _has_enough_data(htf_df, bias_df, poi_df, entry_df) -> str | None:
    """Return None if all frames are big enough, otherwise an error string."""
    if htf_df is None or len(htf_df) < MIN_BARS["htf"]:
        return f"htf needs >={MIN_BARS['htf']} bars, got {0 if htf_df is None else len(htf_df)}"
    if bias_df is None or len(bias_df) < MIN_BARS["bias"]:
        return f"bias needs >={MIN_BARS['bias']} bars, got {0 if bias_df is None else len(bias_df)}"
    if poi_df is None or len(poi_df) < MIN_BARS["poi"]:
        return f"poi needs >={MIN_BARS['poi']} bars, got {0 if poi_df is None else len(poi_df)}"
    if entry_df is None or len(entry_df) < MIN_BARS["entry"]:
        return f"entry needs >={MIN_BARS['entry']} bars, got {0 if entry_df is None else len(entry_df)}"
    return None


def _diagnose(
    htf_bias,
    poi_tap,
    ltf_mss,
    micro_fvg,
    delta,
    require_fvg: bool = True,
    *,
    delta_mode: str = "sign",
    rel_delta: float = 0.0,
    relative_delta_threshold: float = 0.5,
    mfvg_retested: bool | None = None,
    ltf_bias: str = "N/A",
    require_bias_alignment: bool = False,
) -> dict:
    """Per-direction list of what's blocking entry.

    J14 (audit gap #22): under `delta_mode="relative"`, the entry gate
    fires on `rel_delta vs threshold`, not on raw `delta > 0`. The
    blocker text now references whichever value actually drove the
    decision so debug output stays honest.

    Phase D: when `mfvg_retested` is False, both BUY and SELL blocker
    lists get a "MFVG not retested" entry so the TG card surfaces the
    missing piece. `None` (default) means the retest gate is off — no
    blocker reported on either side, matching pre-Phase-D behaviour.
    """
    buy_blockers, sell_blockers = [], []

    # Phase E — bias-alignment blocker. Listed first so the funnel
    # counter attributes the drop-off to the HTF/LTF disagreement
    # rather than to a downstream gate that would also fail.
    if require_bias_alignment and ltf_bias != "N/A" and htf_bias != ltf_bias:
        mismatch_text = f"Bias mismatch: HTF={htf_bias} vs LTF={ltf_bias}"
        buy_blockers.append(mismatch_text)
        sell_blockers.append(mismatch_text)

    if htf_bias != "BULLISH":
        buy_blockers.append(f"HTF bias is {htf_bias} (need BULLISH)")
    if poi_tap != "POI TAPPED":
        buy_blockers.append("POI not tapped")
    if ltf_mss != "BULLISH MSS":
        buy_blockers.append(f"MSS is '{ltf_mss}' (need BULLISH MSS)")
    if require_fvg and micro_fvg != "BULLISH FVG":
        buy_blockers.append(f"FVG is '{micro_fvg}' (need BULLISH FVG)")
    if delta_mode == "relative":
        if rel_delta <= relative_delta_threshold:
            buy_blockers.append(
                f"Relative delta is {rel_delta} (need > {relative_delta_threshold})"
            )
    else:
        if delta <= 0:
            buy_blockers.append(f"Delta is {delta} (need > 0)")
    if mfvg_retested is False:
        buy_blockers.append("MFVG not retested (need a later close inside the gap)")

    if htf_bias != "BEARISH":
        sell_blockers.append(f"HTF bias is {htf_bias} (need BEARISH)")
    if poi_tap != "POI TAPPED":
        sell_blockers.append("POI not tapped")
    if ltf_mss != "BEARISH MSS":
        sell_blockers.append(f"MSS is '{ltf_mss}' (need BEARISH MSS)")
    if require_fvg and micro_fvg != "BEARISH FVG":
        sell_blockers.append(f"FVG is '{micro_fvg}' (need BEARISH FVG)")
    if delta_mode == "relative":
        if rel_delta >= -relative_delta_threshold:
            sell_blockers.append(
                f"Relative delta is {rel_delta} (need < {-relative_delta_threshold})"
            )
    else:
        if delta >= 0:
            sell_blockers.append(f"Delta is {delta} (need < 0)")
    if mfvg_retested is False:
        sell_blockers.append("MFVG not retested (need a later close inside the gap)")

    if len(buy_blockers) <= len(sell_blockers):
        closest, blockers = "BUY", buy_blockers
    else:
        closest, blockers = "SELL", sell_blockers

    total_conditions = 5 if require_fvg else 4
    return {
        "buy_blockers": buy_blockers,
        "sell_blockers": sell_blockers,
        "closest_direction": closest,
        "blockers": blockers,
        "near_miss": len(blockers) == 1,
        "total_conditions": total_conditions,
    }


def _empty_result(pair: str, session: dict, error: str) -> dict:
    """Return a fully-keyed dict when we couldn't evaluate (so UI doesn't break)."""
    return {
        "pair": pair,
        "error": error,
        "price": 0.0,
        "last_close": 0.0,
        "htf_bias": "N/A",
        "ltf_bias": "N/A",
        "ltf_poi": 0.0,
        "poi_tap": "N/A",
        "ltf_mss": "N/A",
        "fvg": "N/A",
        "micro_fvg": "N/A",
        "delta": 0.0,
        "atr_1m": 0.0,
        "entry": "NO ENTRY",
        "sl": 0.0,
        "tp": 0.0,
        "tp2": 0.0,
        "rr": 0.0,
        "confidence": 0,
        "proposed_direction": "BUY",
        "proposed_sl": 0.0,
        "proposed_tp": 0.0,
        "proposed_rr": 0.0,
        "gate_blocked": None,
        "regime": None,
        "diagnostics": {
            "buy_blockers": ["insufficient data"],
            "sell_blockers": ["insufficient data"],
            "closest_direction": "BUY",
            "blockers": ["insufficient data"],
            "near_miss": False,
            "total_conditions": 5,
        },
        "india_time": session["india_time"],
        "tokyo_time": session["tokyo_time"],
        "tokyo_status": session["tokyo_status"],
        "london_time": session["london_time"],
        "london_status": session["london_status"],
        "newyork_time": session["newyork_time"],
        "newyork_status": session["newyork_status"],
        "active_session": session["active_session"],
        "ltf_df": pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"]),
        "poi_df": pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"]),
    }
