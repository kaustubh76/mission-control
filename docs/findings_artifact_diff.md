# Gate diff — external SMC artifact vs. `ICTProMaxStrategy`

Source artifact: a ~450-line Delta-Exchange + Telegram SMC scanner shared
for reference. The artifact runs a 7-step sequential AND-gate on each 1m
close. This doc compares each of its gates to the equivalent in
`src/ictbot/strategy/ict_pro_max.py` and flags what (if anything) is
missing on our side.

Comparison was done against `ict_pro_max.py` at branch `feat/rr2plus-grid`
and indicators under `src/ictbot/indicators/`.

## Gate-by-gate

| # | Artifact step | ictbot equivalent | Status |
|---|---|---|---|
| 1 | **HTF Bias** — compare 4H + 1H swing high vs swing low | `_get_htf_bias()` on `htf_df` via `swing`/`sma`/`slope` engines | ✅ Equivalent. ictbot is strictly better — three pluggable engines, formal `find_swings`, returns `WAITING` instead of forcing a direction. |
| 2 | **5m OB + Fib 50% filter** — only consider OBs whose midpoint sits inside the upper/lower half of the 20-bar swing range | `get_ob_poi()` + `get_poi_tap()` on `poi_df` (3m) or `htf_df` (4h) | ⚠️ Partial. ictbot does OB detection (`find_order_block`) and mitigation retirement, but has **no Fib-zone filter**. Artifact's filter rejects OBs sitting in the "wrong half" of the leg, which is a fairly standard ICT premium/discount check. |
| 3 | **Sweep** — wick pierces 10-bar high/low by ≤0.1%, closes back | None. `indicators/liquidity.py` finds unbroken external liquidity *for TP2 targeting*, not as an entry gate. | ❌ Missing. The artifact requires a liquidity sweep before the MSS. ictbot enters off MSS alone. |
| 4 | **MSS on 3m** — price within 0.1% of recent swing | `get_ltf_mss()` on `poi_df` with `mss_mode="swing"` and `mss_timeframe="poi"` | ✅ Equivalent. ictbot is stricter — real swing break vs the artifact's "near a swing" buffer. |
| 5 | **Displacement** — last 3 candles, body ≥50% of range AND ≥1.3× the 10-bar avg body | None | ❌ Missing. No displacement filter in `ict_pro_max.py` or `indicators/`. |
| 6 | **MFVG** — 3-candle imbalance in last 6 bars | `get_micro_fvg_info()` | ✅ Equivalent + better. ictbot supports `min_formation_time` (Phase C: FVG must form strictly after MSS) and `mitigation_bars` retirement. |
| 7 | **Retrace into MFVG** — current price wicks/closes into the gap | `has_mfvg_retest()` (Phase D, `require_mfvg_retest=True` by default) | ✅ Equivalent. |
| — | (not in artifact) | `delta` / `relative_delta` directional gate | ictbot extra — adds order-flow confirmation the artifact lacks. |
| — | (not in artifact) | Killzone, low-vol ATR-percentile, news blackout | ictbot extra — environmental gating the artifact has no equivalent of. |

## Net assessment

ictbot is structurally more rigorous on every shared gate (better swing
defs, formation-time floors, mitigation retirement, retest confirmation,
broker reconciliation, news blackout). The three gates the artifact has
that we don't:

### 1. Fib 50% / premium-discount filter on POI ⚠️
**Worth considering.** This is a textbook ICT premium-discount check: for
a long, the OB should sit in the *discount* half of the recent swing
range (below 50%). It would slot cleanly into `get_ob_poi()` as an
optional `fib_filter: float | None = None` parameter — pass 0.5 to
require the OB be in the discount/premium half for the bias.

Risk: it will further narrow the funnel. Phase B3 already widened
`require_fvg → False` and `POI_TAP_TOLERANCE 0.0015 → 0.005` to fix a
signal-count floor problem; layering another filter on top without a WFO
run would regress that.

**Recommendation:** prototype on a branch, run `engine.wfo` to check
signal count + expectancy delta vs current main before merging.

### 2. Liquidity sweep gate ❌
**Worth investigating.** The artifact requires a sweep (a wick pierces a
recent swing extreme and closes back) before allowing the MSS. The ICT
canon is "sweep → MSS → FVG → entry", and Phase C already encodes the
"MSS → FVG" ordering via `min_formation_time`. The sweep-before-MSS
constraint is the missing link.

Implementation would be a new `indicators/sweep.py` that returns the
timestamp of the most recent sweep matching bias, and a new strategy
flag `require_sweep_before_mss` that, when enabled, requires
`mss_time > sweep_time`. Same pattern as Phase C's FVG-after-MSS.

**Recommendation:** lower priority than the funnel logging below. The
sweep concept overlaps significantly with what `get_ltf_mss` already
catches when run in swing mode. Quantify the gap empirically before
adding the gate.

### 3. Displacement filter ❌
**Probably skip.** "Body ≥50% of range AND ≥1.3× 10-bar avg body" is a
volatility filter dressed up as a structure filter. ictbot already has:
- `atr_percentile_regime()` — bottom-30% ATR percentile blocks entry
- `mss_mode="swing"` — requires a real break, not a noise bar

Adding a third volatility check on top would be a redundant gate that
mostly suppresses good signals during quiet sessions. Leave it out unless
WFO data argues otherwise.

## Borrowed pattern — per-step funnel instrumentation ✅

The one *operational* idea worth lifting from the artifact: per-step
fail tracking via its `last_step_fail` dict. ictbot already exposes
`diagnostics["blockers"]`, but never aggregates blockers into Prometheus
counters. The complementary change in [runtime/metrics.py](../src/ictbot/runtime/metrics.py)
and [orchestrator/scanner.py](../src/ictbot/orchestrator/scanner.py)
adds:

- `ictbot_funnel_step_failures_total{pair, step, direction}` — first
  blocker in canonical order per non-firing eval. Lets the dashboard
  answer "of the last 1000 evals on BTC, where did we drop off?"
- `funnel_step_failed` structured log line per eval with the same fields.

This pairs naturally with the B3 funnel-widening work — gives a feedback
loop ("widening the FVG gate moved drop-offs from `fvg` to `delta`") that
the team currently has to reconstruct by eyeballing logs.
