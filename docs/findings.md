# Empirical findings

A running log of the non-obvious things the validation pipeline has
turned up. Each entry is a finding, not a recipe — the code that fixed
it is in the commit history. Newest at the top.

---

## 1. Bias was inverted

The 4h SMA crossover lags the move. When it flips BEARISH, BTC has
often already bottomed. Trading WITH the bias means buying after the
dump and selling after the rip — the opposite of what you want. Use
`--invert` (or `STRATEGY_MODE="fade"`) to flip the signal direction.

## 2. Signal spam

The earlier backtest counted 381 "signals" in 1000 bars because
conditions kept evaluating true on consecutive bars. The position-aware
loop in `backtest.py` now blocks new entries while a trade is open,
which is what a real trader does. Signal count dropped from 381 to 10 —
and those 10 became evaluable.

## 3. Best config so far on BTC

5000 1m bars, `make best`:

```
poi_tol=0.005  sl=0.3%  tp=0.9%  no FVG  --invert
→ 6 signals, 3W/3L (50%) → +1.0R expectancy
```

This is not a tuned/finished strategy — it's the baseline the tools
surfaced. Use `make wfo` to validate it doesn't fall apart
out-of-sample.

## 4. Multi-pair bias scoreboard

5000 1m bars, fade mode, default fees+slip:

| Pair | Best Engine | W/L    | Win %  | Net Exp/trade |
|------|------------|--------|--------|--------------:|
| PAXG | slope      | 2/1    | 66.7%  | +1.20R        |
| ETH  | sma        | 6/4    | 60.0%  | +0.93R        |
| BTC  | sma        | 3/3    | 50.0%  | +0.53R        |
| SOL  | slope      | 10/15  | 40.0%  | +0.14R        |
| XRP  | slope      | 14/51  | 21.5%  | **−2.74R**    |

XRP is broken because at ~$1.35 the `round(price, 2)` in SL/TP plus the
fixed-fraction 0.3% stop leaves friction (fees+slip) at ~74% of risk
distance. Avoid tight-fraction strategies on low-priced assets, or
switch to ATR-based stops for them. (Tracked as gap **S1** in
`PLAN.md`.)

## 5. Cross-pair walk-forward verdict

5000 1m bars, fade mode:

| Pair       | Verdict        | TRAIN exp | TEST exp |
|------------|----------------|-----------|---------:|
| SOL        | ✅ holds       | +2.72R    | +1.72R   |
| ETH        | ❌ overfit     | +2.72R    | −1.28R   |
| XRP        | ❌ overfit     | +0.98R    | −1.23R   |
| BTC, PAXG  | no winner — insufficient trades on TRAIN |

The killer insight: **only SOL has an edge that survives
out-of-sample**. ETH and XRP look profitable in the bias_compare
scoreboard but those were curve-fits. Don't deploy them.

## 6. Friction is brutal on tight stops

The backtest subtracts fees + slippage from every closed trade:

```
friction_R = 2 × (FEE_PER_SIDE + SLIPPAGE_PER_SIDE) / risk_distance_pct
net_R      = gross_R − friction_R
```

Defaults in `config.py` (Bybit perpetuals taker): `FEE_PER_SIDE=0.0005`,
`SLIPPAGE_PER_SIDE=0.0002`. With a 0.3% SL strategy, friction is
~0.47R per trade. The tighter your SL, the more sensitive you are.

## 7. Trail-to-break-even cuts both ways

Once price moves +N R in your favor, move SL to entry. This eliminates
losing trades that came close to TP and reversed.

Live finding: trail-to-BE on the BTC/ETH/SOL/XRP fade strategy
dramatically cuts loss magnitude on TEST (SOL went from −1.72R to
−0.20R) but also caps winners. Net: smaller drawdowns, smaller wins,
near-zero expectancy. Use it when capital preservation matters more
than maximizing edge.

## 8. Kelly is brutal on drawdowns

At 50% win-rate / 1:3 RR:

- Half-Kelly (16.7% per trade) → **100% chance of 50% drawdown in 500
  trades** (but median outcome is +7.3×)
- 1% per trade → **0% chance of 50% drawdown**, median +131×

This is why pros bet 1–2%, not Kelly.

## 9. Phase 3 silently activated stale .env values

The old `config.py` used `python-dotenv` to load `.env` into
`os.environ` but never read most of the keys back — it just hard-coded
constants. So legacy `.env` entries like `HTF_TIMEFRAME=15m`,
`SL_PERCENT=0.5`, `LOOP_INTERVAL_SECONDS=10` were dead weight from a
previous 2-timeframe version of the bot.

The Phase 3 migration to `pydantic-settings` changed this. Pydantic
reads any env var matching a field name (`case_sensitive=False`) and
overrides the default. The stale `HTF_TIMEFRAME=15m` in `.env`
overrode the `"4h"` default, so the analyzer was running with **HTF =
15m** (= same as the LTF-bias frame), collapsing the multi-timeframe
alignment that the entire strategy is built on.

Detection took finding `htf=72 bars spanning 18 hours` in a debug dump
(72 4h bars should span 12 days). Fix: strip the legacy keys from
`.env`. Lesson: pydantic-settings is convenient but every key now has
to be intentional. Tracked in `PLAN.md` if it ever bites again.

## 10. `_bars_needed` off-by-one cost 93% of replay bars

`backtest._bars_needed("4h", 5000, 50)` used floor division:
`(5000 // 240) + 50 = 70`. But the 1m replay window actually spans
`5000 / 240 = 20.83` 4h bars, not 20. So at `T_start` the 4h slice had
`70 − 21 = 49` bars — one below `MIN_BARS["htf"] = 50`. The
INSUFFICIENT_DATA check failed for the entire warmup-after-start
period (≈4 hours of 1m bars).

Why nobody noticed for months: the historic baseline in §5 happened
to run at a time-of-day where the 4h bar alignment was favourable
(latest 4h close near "now" → effective offset = 0). Once Bybit's
latest 4h bar was a couple of hours stale relative to "now", the
math tipped the other way and 93.8% of bars failed the check.

Fix: ceiling division + a `+1` buffer in `_bars_needed`. Regression
test in `tests/test_bars_needed.py`.

## 11. Strategy v2 (mss=swing + mitigation) zeros out every pair

5000 1m bars, fade mode, --quick grid (16 combos), with both bugs
above now fixed:

| Pair  | Baseline (mss=simple)      | v2 (mss=swing, mitigation=10) |
|-------|----------------------------|-------------------------------|
| BTC   | no TRAIN winner            | no TRAIN winner               |
| ETH   | no TRAIN winner            | no TRAIN winner               |
| SOL   | TRAIN 2W/3L +1.13R, TEST: no closures | no TRAIN winner   |
| XRP   | no TRAIN winner            | no TRAIN winner               |
| PAXG  | no TRAIN winner            | no TRAIN winner               |

Two readings:

- **The strategy is signal-starved in current conditions.** Even with
  the most permissive baseline knobs, 4 of 5 pairs produce <3 closed
  trades in a 2500-bar TRAIN window. The §5 measurement that gave
  SOL +1.72R OOS was from a more volatile market regime; recent BTC
  has been range-bound on the 4h, so HTF bias rarely aligns with a
  POI tap + MSS confluence.

- **v2 makes it worse, not better.** Swing-MSS requires breaking a
  protected swing pivot (vs. simple's 2-bar high/low rule), and
  mitigation retires POIs after first tap. Both REDUCE signal
  frequency. With the strategy already starved, adding strictness
  drops eligible signals to zero.

The actionable takeaway: **don't ship Phase 6 knobs to live trading
without first proving they don't break worse than they fix.** The
fixes are theoretically correct (gap S2/S3/S4/S5) but empirically
they shrink a signal set that was already too small. Options:

  1. Loosen another constraint to compensate (`require_fvg=False`,
     wider `poi_tol`, ATR-based stops).
  2. Run the FULL 72-combo grid not `--quick`, to widen the search.
  3. Wait for a more volatile regime and re-measure.
  4. Use the new knobs only as opt-ins, not as defaults.

Option 4 is what the code already does — `mss_mode` defaults to
`"simple"`, `mitigation_bars` defaults to `None`. So we ship safely
without forcing the strictness on existing users.

## 12. 10000-bar / full-72-combo grid: no pair has a robust OOS edge

After ruling out small-sample noise (§11) with a wider experiment —
10 000 1m bars (≈ 7 days), the full 72-combo grid, `train_frac=0.5`,
fade mode. Verdicts shown below use the corrected `classify()` helper
(see §13) that requires TRAIN > 0 for "holds":

| Pair  | Verdict       | TRAIN exp | TEST exp | TRAIN W/L | TEST W/L | Winning cfg                                  |
|-------|---------------|-----------|---------:|-----------|---------:|----------------------------------------------|
| BTC   | no edge       | −0.27R    | +0.53R   | 1/4       | 2/4      | poi=0.0015, sl=0.003, tp=0.015, fvg=True     |
| ETH   | overfit       | +0.33R    | −0.22R   | 3/7       | 5/19     | poi=0.005, sl=0.003, tp=0.015, fvg=False     |
| SOL   | overfit       | +2.56R    | −0.34R   | 2/1       | 6/26     | poi=0.005, sl=0.003, tp=0.015, fvg=True      |
| XRP   | no edge       | −0.17R    | −0.37R   | 3/6       | 5/8      | poi=0.0015, sl=0.008, tp=0.015, fvg=False    |
| PAXG  | ✅ holds      | +0.52R    | +0.22R   | 3/2       | 1/1      | poi=0.01, sl=0.005, tp=0.01, fvg=True        |

Two pairs (BTC, XRP) flipped from a misleading "holds" /"overfit"
under the old verdict to "no edge" — the sweep never found a
profitable in-sample config, so whatever TEST showed was noise. Only
PAXG legitimately holds, but with TEST W/L = 1/1 (n=2) it's not yet
a statement.

Three honest readings:

1. **PAXG is the only pair with a directionally consistent edge** —
   positive in both halves. But TEST W/L is 1/1 (n=2): not a
   statement, just one trade and one loser.

2. **The historic SOL +1.72R OOS verdict (§5) did NOT replicate** —
   on this 10k-bar window SOL is the worst overfit (TRAIN 2/1 →
   TEST 6/26). The §5 measurement caught a favourable regime; the
   strategy did not catch a durable edge.

3. **Curve-fitting is widespread.** Three pairs show classic
   overfit (positive or marginal TRAIN, negative TEST). The
   72-combo sweep is finding what worked in the past 3.5 days and
   failing in the next 3.5.

**What this means for execution wiring (Phase 8.5):** the strategy
as currently tuned does not have a measurable, OOS-validated edge
in the current 7-day market regime. Three plausible next steps:

  a. **Wait for a different regime + remeasure.** ICT setups
     historically work best in trending markets with clear killzone
     liquidity grabs. The last week of BTC has been range-bound.
  b. **Go longer.** 20 000–50 000 1m bars (2–6 weeks of data). The
     marginal cost is one cache warm + a few minutes of sweep time,
     which the Phase 5 parquet cache already pays for.
  c. **Structural changes, not parameter sweeps.** The strategy
     currently does 4-TF alignment + POI tap + 2-bar MSS + delta.
     Maybe swap delta for a real CVD (gap S6), or use ATR-scaled
     stops (`--sl-atr`, `--tp-atr`) to normalise across pairs,
     or pick a completely different setup (mean-revert on the 1m
     wick into the prior 4h high/low, e.g.).

**Recommendation:** do (b) first because it's cheap and rules out
"too little data" as the cause. If 20k bars still shows the same
overfit pattern, the strategy itself is the problem — not the
sample size — and (c) is where we go.

## 13. 20000-bar / fade mode: the strategy has no edge anywhere

Took (b) from §12 — bumped to 20 000 1m bars (≈ 14 days), same full
72-combo grid, same fade mode. Result is decisive:

| Pair  | Verdict       | TRAIN exp | TEST exp | TRAIN W/L | TEST W/L | RR  |
|-------|---------------|-----------|---------:|-----------|---------:|----:|
| BTC   | no edge       | −0.22R    | −0.32R   |  6/11     |  9/19    | 2.0 |
| ETH   | no edge       | −0.29R    | −0.36R   |  9/14     |  9/16    | 1.2 |
| SOL   | no edge       | −0.34R    | −0.15R   | 10/17     | 15/18    | 1.2 |
| XRP   | no edge       | −0.34R    | −0.83R   |  3/ 8     |  5/20    | 3.0 |
| PAXG  | no closures   | +0.20R    | n/a      |  1/ 2     |  0/ 0    | 3.1 |

**Four of five pairs cannot find a profitable in-sample config**
out of 72 grid combinations on 14 days of data. PAXG technically
fits but with 3 closed TRAIN trades and 0 TEST closures, there's
nothing to evaluate.

This kills the "give it more data" theory. The strategy as
implemented does not have an edge in fade mode.

**The mechanism: friction vs. realised RR.** The sweep keeps
picking tight-stop / tight-target configs because they generate
enough closed trades to be evaluable, but tight stops mean
friction eats the entire gross edge. Worked example for BTC:

```
Winner cfg: sl=0.005, tp=0.010, rr = 2:1.
TRAIN win-rate: 6/(6+11) = 35.3%.
Gross expectancy:    0.353 × 2 − 0.647 × 1 = +0.06R per trade.
Friction per trade:  2 × (0.0005 + 0.0002) / 0.005 = 0.28R.
Net expectancy:      +0.06 − 0.28 = −0.22R per trade.   ✓ matches.
```

Gross is *positive* (barely). After realistic fees + slippage at
a 0.5 % stop, the strategy bleeds money.

**One algebraic surprise** — and the next experiment. fade ↔ follow
is a clean inversion: same signal trigger, opposite direction, same
price action ⇒ fade WIN ≡ follow LOSS. So inverting the W/L of the
fade winners gives the *implied* follow-mode performance:

| Pair  | Fade TRAIN W/L  | Implied follow TRAIN W/L | Implied follow TRAIN exp (rr, friction-net) |
|-------|-----------------|---------------------------|----------------------------------------------|
| BTC   |  6/11 (35.3 %)  | 11/6  (64.7 %, rr=2.0)    | gross +0.94R − 0.28R = **+0.66R**             |
| ETH   |  9/14 (39.1 %)  | 14/9  (60.9 %, rr=1.2)    | gross +0.34R − 0.18R = **+0.17R**             |
| SOL   | 10/17 (37.0 %)  | 17/10 (63.0 %, rr=1.2)    | gross +0.39R − 0.18R = **+0.21R**             |
| XRP   |  3/ 8 (27.3 %)  |  8/3  (72.7 %, rr=3.0)    | gross +1.91R − 0.28R = **+1.63R**             |

If the algebra holds in a real run, **follow mode would show a
positive edge on every pair in the current 14-day window**. That
flips finding §1 (which said fade was right because the 5k-bar SMA
lagged) — at 14-day samples, the SMA bias may actually lead correctly
and we want to trade with it, not against it.

This is the headline empirical claim of the migration sessions: the
strategy v1 we inherited optimised for the wrong direction.

The follow-mode pass is the next-section finding. Until it lands,
the formal status is: **don't deploy fade. Investigate follow.**

## 14. 20000-bar / follow mode: partial confirmation, no green light

Re-ran the same 20 000-bar / 72-combo sweep with `INVERT = False`
(follow mode). Verdict pattern is qualitatively different from §13:

| Pair  | Verdict       | TRAIN exp | TEST exp | TRAIN W/L | TEST W/L | RR  |
|-------|---------------|-----------|---------:|-----------|---------:|----:|
| BTC   | ✅ holds      | +0.88R    | +0.20R   |  2/ 2     |  2/ 4    | 3.1 |
| ETH   | ❌ overfit    | +0.97R    | −0.16R   |  3/ 5     |  3/13    | 5.0 |
| SOL   | ❌ overfit    | +1.71R    | −0.45R   |  3/ 3     |  4/25    | 5.0 |
| XRP   | ✅ holds      | +0.88R    | +0.28R   |  2/ 2     |  2/ 4    | 3.1 |
| PAXG  | ❌ overfit    | +3.20R    | −1.47R   |  2/ 2     |  0/ 7    | 8.3 |

**Confirmed: follow beats fade structurally.** Every pair has
*positive* TRAIN expectancy (vs. all-negative in fade §13). The
hypothesis from §13 — that we'd been inverting away an edge —
holds qualitatively.

**Where the algebra was wrong** (vs. the §13 prediction): the sweep
picks materially higher-RR configs in follow mode (3:1 to 8:1, not
the 1.2–2.0 fade picked). At 50 %+ WR, high RR amplifies gross more
than friction can erode, so the sweep naturally drifts there. TRAIN
expectancies are 30 %–90 % higher than I predicted.

**Where the result is weak: tiny samples.** 4 of 5 winners have
TRAIN W/L of 2/2 or 3/3 — 4–6 closed trades over a 7-day TRAIN slice.
The TEST holders (BTC, XRP) have 6 closed trades each. With samples
that small, the 95 % CI on expectancy is roughly ±1R; the +0.20R /
+0.28R TEST holds could easily be noise.

**Three of five pairs (ETH, SOL, PAXG) classic-overfit:** strong
TRAIN, negative TEST. PAXG is the most dramatic — TRAIN +3.20R from
4 trades, TEST −1.47R from 7. That's a sweep latching onto an
8:1 RR fluke in TRAIN that fails completely OOS.

**Path to deploy:**

  1. **Don't deploy yet.** No pair clears a reasonable bar: ≥ 10
     closed trades on TEST AND TEST expectancy ≥ +0.3R AND
     TRAIN/TEST agreement (both positive). Today only BTC + XRP
     have any directional agreement and both fail the trade-count
     bar.
  2. **Longer measurement window.** 50 000 bars (≈ 5 weeks) would
     put expected TRAIN+TEST closures into the 15–30 range, where
     the verdicts mean something. The Phase 5 parquet cache makes
     this cheap on re-runs.
  3. **ATR-based stops, not fixed-fraction.** The sweep is locked
     to fixed `sl_frac` × `tp_frac` pairs, which means picking one
     RR for all market regimes. ATR-scaled stops (`--sl-atr 1.0
     --tp-atr 3.0`) auto-widen during volatile regimes and tighten
     in quiet ones, decoupling RR from friction.
  4. **Per-pair regime tagging.** If the ATR-percentile regime
     (`indicators/regime.py`) explains some of the in-vs-out
     divergence, we have a feature to gate on, not just discard
     trades.

**Bottom line of the migration audit:**
- Strategy v1 (fade) was net-negative on 14-day BTC/ETH/SOL/XRP
  even at the best of 72 configs — friction killed a marginal
  gross edge.
- Strategy v1 inverted (follow) shows positive TRAIN on every pair
  and OOS-positive on BTC + XRP, but n is too small to deploy.
- Phase 8.5 (live broker wiring) remains gated until either a
  longer window (option 2) or a different SL/TP framing (option 3)
  produces a robust edge with adequate sample size.

The repo is shippable as a paper-trading + research scaffold.
It is not yet shippable as a live-trading system.

## 15. 50000-bar / follow mode: the §14 holds were small-sample noise

Re-ran the same 72-combo follow-mode sweep at 50 000 1m bars
(≈ 35 days), full 72-combo grid. Bigger samples; cleaner verdicts.

| Pair  | Verdict     | TRAIN exp | TEST exp | TRAIN W/L | TEST W/L  | n   | Winning RR |
|-------|-------------|-----------|---------:|-----------|----------:|----:|----:|
| BTC   | no edge     | −0.43R    | −0.15R   | 12/24     | 20/24     |  80 | 1.2 |
| ETH   | no edge     | −0.25R    | −0.02R   | 26/37     | 43/41     | 147 | 1.2 |
| SOL   | ❌ overfit  | +0.02R    | −0.27R   |  9/22     |  9/32     |  72 | 3.1 |
| XRP   | ✅ holds    | +0.25R    | +0.00R   |  3/12     |  6/19     |  40 | **5.0** |
| PAXG  | ✅ holds    | +0.72R    | +0.22R   |  2/ 4     |  2/ 6     |  14 | **5.0** |

**The 20k follow-mode holds for BTC and ETH evaporated:**
- BTC went from "✅ holds +0.88R / +0.20R, n=8" → "no edge −0.43R /
  −0.15R, n=80". The earlier hold was 8-sample noise crossing zero.
- ETH went from "❌ overfit +0.97R / −0.16R, n=24" → "no edge
  −0.25R / −0.02R, n=147". The sweep can't find a profitable
  in-sample config when forced to evaluate over 25k bars.

**The new finding — RR is the lever.** XRP and PAXG both held OOS
on `rr = 5` configs (sl=0.005, tp=0.025). The four no-edge /
overfit pairs picked low-RR (1.2–3.1) configs. The mechanism is
friction:

> Net expectancy = (WR × RR − (1 − WR)) − friction.
> At low RR (1.2) and 40 % WR, gross = −0.12R; friction adds
> 0.18–0.28R loss. You can't win.
> At RR = 5 and 40 % WR, gross = +1.4R; even 0.28R friction
> leaves +1.1R net. The math forgives you.

The full-grid sweep is structurally biased toward tight-RR configs
because they fire more signals (passing the ≥ 3 closures filter),
even though they're net-negative once friction is paid. **The
sweep is optimising for "evaluable", not for "profitable".**

**Engine perf side note.** The 50k sweep originally projected to
~2.5 hours per mode. We profiled with cProfile and found
`get_atr` was 67 % of wall time. Three engine fixes landed
alongside this experiment (see `PLAN.md` §4.5 P1): `tail(period+1)`
slice in ATR, `np.searchsorted` for time-window slicing, and a
delta prefix-sum monkey-patch. Net: **6× speedup**, sweep completed
in ~30 minutes.

## 16. Final plan — engineering toward a ≥ 2:1 RR strategy with an edge

Synthesising §1 – §15, here's a concrete strategy roadmap that
constrains the search to **trade-level RR ≥ 2:1** as a hard floor,
with empirical milestones at each step. The goal is **per-trade
net expectancy ≥ +0.5R, with risk:reward ≥ 1:2 on every trade** —
the kind of profile §5 / §8 say you actually need to survive
Kelly + drawdown realities.

### Step 1 — Constrain the sweep grid to RR ≥ 2:1

The current default grid includes (sl, tp) pairs that yield RR as
low as 1.2 (e.g. sl=0.008, tp=0.010). At those numbers, gross edge
is structurally drowned by friction (§15). Strip them. Proposed grid:

| sl    | tp     | RR  |
|------:|-------:|----:|
| 0.003 | 0.010  | 3.3 |
| 0.003 | 0.015  | 5.0 |
| 0.003 | 0.025  | 8.3 |
| 0.005 | 0.015  | 3.0 |
| 0.005 | 0.025  | 5.0 |
| 0.008 | 0.025  | 3.1 |

6 RR-respecting (sl, tp) × 4 `poi_tol` × 2 `require_fvg` = 48
combos. New grid in `ictbot.engine.sweep` under `GRIDS["rr2plus"]`.

**Acceptance bar:** rerun WFO on 50k bars with the new grid; at
least **2 pairs must show TRAIN > 0 AND TEST > 0 AND ≥ 20 closed
TEST trades**. (XRP and PAXG already cleared this at the old grid
with RR=5 configs.)

### Step 2 — ATR-scaled stops, not fixed-fraction

Fixed-fraction stops force every pair to the same friction ratio
regardless of its volatility regime. ATR scaling (sl = 1×ATR,
tp = 3×ATR) auto-widens in volatile regimes and tightens in calm —
keeping RR constant while friction tracks risk. Flags exist
already (`--sl-atr`, `--tp-atr` in `backtest.py`); add them to a
new `GRIDS["atr"]`:

| sl_atr | tp_atr | implied RR |
|-------:|-------:|-----------:|
| 0.5    | 1.5    | 3.0        |
| 0.5    | 2.5    | 5.0        |
| 1.0    | 2.0    | 2.0        |
| 1.0    | 3.0    | 3.0        |
| 1.0    | 5.0    | 5.0        |
| 1.5    | 3.0    | 2.0        |

6 (sl_atr, tp_atr) × 4 `poi_tol` × 2 `require_fvg` = 48 combos.

**Acceptance bar:** ATR grid clears the same Step 1 bar on ≥ 3
pairs (improved from ≥ 2).

### Step 3 — Widen the signal funnel

At RR = 5, the strategy needs only ~17 % WR to be net-positive
after friction. So the constraint is no longer "is this a good
setup?" but "do we have enough setups to validate WR?" The 4-of-4
ICT condition (POI tap + MSS + FVG + delta-sign) fires ≈ 3–40
signals per 25k TRAIN bars across pairs — too sparse for high-
confidence WR estimation.

Funnel widening options to A/B:
- **Drop the FVG requirement** by default (`require_fvg=False`).
  Every §15 hold already used `fvg=False`.
- **Widen `poi_tap_tolerance`** to 0.005–0.01 default.
- **Replace `delta > 0` with `delta > median_delta(20)`** — a
  *relative* delta condition that adapts to thin-volume bars
  instead of failing them outright.

**Acceptance bar:** signal count per pair per 25k TRAIN bars ≥ 30,
without dropping below ≥ 35 % WR at RR ≥ 3.

### Step 4 — Regime gate

`ictbot.indicators.regime.atr_percentile_regime` already
classifies each bar as HIGH_VOL / LOW_VOL / NORMAL. Hypothesis:
ICT setups need real displacement to work — they should pay off
in HIGH_VOL and lose in LOW_VOL (mean-reverting chop). Strategy
exposes `skip_in_low_vol=True`.

**Acceptance bar:** strategy WR conditional on HIGH_VOL ∪ NORMAL
is ≥ 5 pp higher than overall WR, OR ≥ 0.2R per trade higher
expectancy. If yes, lock the gate in. If no, remove it.

### Step 5 — Paper-trade for 30 days

Once Steps 1–4 produce a config that passes the
"≥ 2 pairs / TRAIN > 0 / TEST > 0 / n ≥ 20" bar, paper-trade for
30 calendar days using `ictbot.exec.paper.PaperBroker`. Run a
parallel backtest over the same wall-clock window and compare
equity curves.

**Acceptance bar:** paper-trade net expectancy within ±0.2R of
the backtest expectancy, over ≥ 30 closed paper trades. This is
the bar `PLAN.md` §5.1 set.

### Step 6 — Flip `ENABLE_LIVE_TRADING` for one pair, one position

Only after Step 5 passes. Start with the strongest pair (today:
XRP or PAXG). Cap risk at **0.5 % of equity per trade**, max one
open position, daily loss limit 1R. Caps are already wired in
`ictbot.portfolio.caps` (Phase 8). Phase 8.5 (live broker wiring)
becomes a small ccxt PR — the orchestration is already there.

**Acceptance bar:** 30 calendar days live with no cap breach AND
PnL within ±0.5R of the backtest projection for the observed
signal count.

---

### Why these steps in this order

- **Step 1 is the cheapest, most consequential change.** Just
  removes loss-prone configs from the grid. If §15's RR mechanism
  generalises, Step 1 alone may produce 2–3 robust pairs.
- **Steps 2–4 are layered improvements**, each cheap to add and
  test in isolation, each with a measurable acceptance bar.
- **Steps 5–6 are execution polish**, deferred until the
  strategy actually has a measured edge. Live trading without a
  measured edge is just a more efficient way to lose.

### Where it can still fail

If Steps 1–4 don't lift ≥ 3 pairs above the "TRAIN+TEST positive,
n ≥ 20" bar, the honest read is that **the inherited ICT
4-condition setup is not a tradeable edge on Bybit 1m perpetuals
at retail friction**. That's a structural finding, not a tuning
failure. At that point the options are:

1. **Different markets** — lower-friction venues (no taker fee
   below VIP, or coin-margined contracts with maker rebates).
2. **Longer timeframes** — 5m or 15m entries, where the
   friction-to-range ratio is naturally smaller.
3. **Different setup** — mean-revert on the 1m wick into the
   prior 4h high/low, momentum break of an opening-range high,
   etc. The execution scaffold is strategy-agnostic; we just
   subclass `Strategy` and replace `ICTProMaxStrategy`.

Whichever path, the engineering work (data layer, caches, caps,
paper broker, structured logging) survives. Only the strategy
contents change.

---

## 17. (in progress) rr2plus + gates A/B at 50k bars

**Date:** 2026-05-27. **Status:** running. **Branch:** `feat/rr2plus-grid`.

This is the empirical follow-up to §16 Step 1. The harness
(`scripts/wfo_gates_ab.py`) sweeps every pair across four gate cells
— `baseline / killzone / regime / both` — using the new
`GRIDS["rr2plus"]` (48 combos, every (sl,tp) at RR ≥ 2:1). All
verdicts pass through the F3 small-sample gate (TEST closures ≥ 10
required for "✅ holds").

### What's locked in regardless of outcome

- `GRIDS["rr2plus"]` and `GRIDS["atr"]` — opt-in via `--grid` CLI.
- F3 small-sample gate — no more PAXG-style 2/6 = "holds"
  misclassifications.
- F2 bias-SMA prefetch — engine now runs the 48-combo full grid in
  meaningfully under the original 30-minute window.
- E5 bar-time sessions — killzone gating reflects each bar's UTC,
  so the killzone cell is no longer a constant across the run.
- B4 plumbing — every gate combination is reachable from the CLI.

### Acceptance bars (B4)

For at least one gate cell vs baseline, **on the same pair**:
- Δ expectancy ≥ +0.2R, OR
- Δ win-rate ≥ +5pp.

Cells that pass for ≥ 1 pair → lock that gate setting into the
strategy defaults (with regression tests). No cell passes for any
pair → leave both gates opt-in; no complexity added.

### How to populate this section after the run

The script ends with a per-pair table and a closing acceptance
block. Paste the closing block here verbatim plus a one-line
verdict per pair (e.g. "XRP — killzone CLEARS bar; expectancy
+0.34R, WR +6.1pp; lock killzone_required=True for XRP").

Open questions to settle in the writeup:

1. Did any cell beat baseline on ≥ 2 pairs? (criterion for
   strategy-default flip vs per-pair config.)
2. Did the rr2plus grid surface ≥ 3 holders (B1's stretch goal),
   or is it still the §15 XRP+PAXG duo?
3. Does the killzone gate's lift correlate with TEST closures
   (i.e. is the boost coming from filtering noise out of the
   non-killzone hours, or from a real edge inside the killzone)?

### Next step if no gate clears the bar

§16 Step 3 (B3 widen funnel) — already implemented at strategy
level (`POI_TAP_TOLERANCE=0.005` default, `require_fvg=False`
default, `delta_mode="relative"` opt-in). Re-run the same 4-cell
harness with `--delta-mode relative` to test whether normalised
delta carries information the binary-sign one missed.
