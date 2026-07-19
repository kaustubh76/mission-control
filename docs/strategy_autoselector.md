# Strategy auto-selector — the honest "switching engine"

A forward-gated selector that monitors every arm and recommends the best one — **without** the two
traps people expect from a "switching engine": it does **not** promise profit, and it does **not** chase
whatever recently spiked. It picks the best **risk-adjusted FORWARD** performer among **DQ-safe** arms,
with **anti-chasing hysteresis**, and is proven on the SIM book before anything touches live.

`src/ictbot/runtime/strategy_evaluator.py` · driver `scripts/strategy_evaluator.py` (+ `.sh` cron).

## How it decides
1. **Rank** every registered arm by a risk-adjusted score (reuses `contest_readiness.run_readiness`
   + `ab_regime`'s `return − worst_dd` shape): forward-eligible arms score on their UNSEEN forward track
   (`median_weekly_ret − worst_7d_dd`), else fall back to backtest `total_return − survival worst_week_dd`.
2. **Gate to DQ-safe candidates** — `survival.passed` AND stability ≠ `UNSTABLE`. A survival-fail or
   unstable arm is never a candidate.
3. **Champion** = `contest_readiness.recommend_arm` (strict: READY + stability **ROBUST** + forward-eligible
   on a **non-vacuous** track).
4. **Anti-chasing hysteresis** — recommend a switch from the incumbent (current live arm) to the champion
   ONLY if the champion (a) beats the incumbent's score by `STRATEGY_SELECT_MARGIN` (default 0.02) AND
   (b) has held the champion slot for `STRATEGY_SELECT_SUSTAIN` consecutive evals (default 2). Otherwise
   **STAY**. State persists in `data/reports/strategy_evaluator_state.json`.

## What it does with the decision
- **SIM: auto-drives** the paper book (`strategy_select.save`) on a switch — `STRATEGY_AUTO_SELECT_SIM`
  (default **True**). This is the live proof that the engine picks + trades correctly, risking nothing.
- **LIVE: recommend-only** — writes `data/reports/strategy_evaluation.json` (surfaced on the dashboard's
  **Auto-selector** card); the operator applies with `scripts/live_tick.sh <arm>`. The contest-safety
  boundary stays intact (`run_allocator` makes LIVE ignore dynamic selection by design).
- **Opt-in live auto-apply** — `STRATEGY_AUTO_APPLY_LIVE` (default **False**) would also rewrite the live
  `STRATEGY_NAME`. Built but inert; flip it only when you trust the loop.

## Run it
```bash
python scripts/strategy_evaluator.py --dry-run    # compute + print, write nothing
python scripts/strategy_evaluator.py              # evaluate + persist + sim-drive + write recommendation
# cron after the forward sweep (forward_tracks @ 43 6,18):
50 6,18 * * *  "<repo>/scripts/strategy_evaluator.sh"   # bnb-strategy-evaluator
```

## Today's behaviour (the anti-chasing proof)
With no arm forward-eligible yet, it returns **STAY** — it deliberately does **not** chase
`mean_reversion`'s recent +5.99% (3-day, not forward-eligible, its own code flags it as likely
sample-luck). It will only promote an arm once that arm has *earned* it on unseen data. Knobs:
`STRATEGY_SELECT_MARGIN`, `STRATEGY_SELECT_SUSTAIN`, `STRATEGY_SELECT_FORWARD_MIN_DAYS` (default 5d).
