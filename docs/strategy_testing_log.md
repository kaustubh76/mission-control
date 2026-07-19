# BNB Hack — Rigorous Testing Campaign (running log)

**Status: IN PROGRESS.** This is the step-by-step execution log for hardening the strategy
validation campaign — the stability harness + the test-suite gaps. Updated after every step with the
actual results. Plan: [`/Users/apple/.claude/plans/bro-i-have-an-ethereal-unicorn.md`]. Companion to
the process runbook [strategy_campaign.md](strategy_campaign.md).

> **Premise (carried over).** No proven long-only edge on the 8-token universe; arms are
> capability/risk plays. A single-window backtest PASS/FAIL is **noisy** — `breakout` swung
> 14.3% → 31.6% worst-week DD as the data window moved. So this campaign measures *trustworthiness*
> (verdict stability) and locks down behavior with unit + property + safety tests. The locked
> `momentum_adaptive` default stays bit-for-bit throughout.

## Progress

| Step | What | Status | Result |
|---|---|:--:|---|
| **S1** | Adapter mechanism tests (4 arms) | ✅ | 10 tests pass · ruff clean |
| **S2** | `verdicts.py` merge/atomic/degrade | ✅ | 5 tests pass · ruff clean |
| **S3** | Forward machinery (synthetic journals) | ✅ | 8 new (+7 promote) = 15 pass |
| **S4** | Property tests (campaign + forward) | ✅ | 6 properties pass (many examples) |
| **S5** | Stability harness + `make stability` | ✅ | built + run live · **dual_momentum most ROBUST; locked default only FRAGILE; breakout UNSTABLE** |
| **S6** | Contest-safety tests + forward runbook | ✅ | 4 tests pass · runbook added (§7.1) |
| **S7** | Coverage + full sweep gate | ✅ | **1357 pass** · ruff clean · validate_allocator PASS |
| **S8** | Parameter sweep (`make sweep_arms`) | ✅ | **breakout UNSTABLE → ROBUST** at entry20/exit5/rb3 · +9 tests |

**Campaign complete.** +51 new tests (1315 → 1366), ruff clean, `make validate_allocator` unchanged
PASS (18.1% DD ✅), registry bit-for-bit green. Coverage: `strategy_campaign` 81%, `strategy_stability`
87%, `forward_promote` 71%.

---

## S1 — Adapter mechanism tests ✅

Closed the real gap: `dual_momentum`, `rotation`, `breakout`, `mean_reversion` had no dedicated
behavior tests (only registry equivalence). Each now asserts its *distinguishing* mechanism on
deterministic synthetic close matrices (no network — pure numpy):

- **`test_adapter_dual_momentum.py`** — the **basket absolute-momentum kill**: with 3 cols crashing +
  1 slow riser, the basket index is down so the whole book → USDT, *even though* the riser passes the
  per-token cash filter (proven by the base abs_filter path still holding it). Basket-up → kill never
  fires → bit-for-bit equal to the base path.
- **`test_adapter_rotation.py`** — holds **exactly top_k=3**, **no cash filter** (stays deployed on a
  broad mild downtrend where the momentum arm goes all-USDT), and equals the multi-lookback
  `weight_path_ranked(blend={120:0.6,60:0.4})`.
- **`test_adapter_breakout.py`** — the **Donchian membership state machine**: enter above the prior
  20-bar high, exit below the prior (shorter) 10-bar low (asymmetric); held set respects the regime cap.
- **`test_adapter_mean_reversion.py`** — the **oversold trigger** fires only on a column trading
  >1σ below its 20-bar mean (flat cols have z==0 via the std==0 guard, never fire); inverse-vol, capped.

**Verify:** `pytest -q tests/test_adapter_*.py` → **10 passed**. `ruff` clean.

## S2 — `verdicts.py` store ✅

`tests/test_verdicts.py` (5 tests): `record()` merges `survival`/`forward` under `[strategy][kind]`
without clobbering the other kind or other strategies; the write is **atomic** (no leftover `.tmp`)
and **canonical** (`sort_keys`, `indent=2`); `load()` degrades to `{}` on both missing and corrupt
files. The verdict store is what the dashboard badges from — now pinned.

**Verify:** `pytest -q tests/test_verdicts.py` → **5 passed**. `ruff` clean.

## S3 — Forward machinery (synthetic backdated journals) ✅

`tests/test_forward_machinery.py` (8 tests) drives `forward_promote._verdict_for` end-to-end on
real journal-shaped rows with known-answer NAV tracks + boundaries: rising→eligible,
declining→not, **flat→median 0→eligible** (0 ≥ 0), below-MIN_ROWS→insufficient, **span exactly at
`min_days`** (4.5d insufficient / 5.4d evaluable), non-positive NAV dropped, non-REBALANCE rows
ignored, only the named strategy counted. Complements the tuple-level math in
`test_forward_promote.py`.

**Verify:** `pytest -q tests/test_forward_machinery.py tests/test_forward_promote.py` → **15 passed**.

## S4 — Property tests (hypothesis) ✅

Invariants that must hold for *any* input (repo convention `@settings(deadline=None)`):
- **`test_campaign_properties.py`** — `_stage` honesty (`Stage 5 ⟺ signed-off AND survives`; never 5
  next to a survival FAIL; each rung implies its predecessors); `_rank_key` total order (errored arms
  last, PASS before FAIL); `splice_guardian` idempotency + exactly one marker pair; `survival_payload`
  exact 6-key set.
- **`test_forward_properties.py`** — `_forward_stats` bounds (`worst_7d_dd ∈ [0,1]`, `tpw ≥ 0`,
  `n_rows == len`) + the None-return contract (only `<MIN_ROWS` or `span<min_days`); `_eligible ⟺` the
  three conditions, checked against the explicit formula.

**Verify:** `pytest -q tests/test_campaign_properties.py tests/test_forward_properties.py` → **6 passed**.

## S5 — Stability harness ✅ (the headline)

Built [`scripts/strategy_stability.py`](../scripts/strategy_stability.py) + `make stability`. For each
arm it reuses the validated backtest engine on ONE fetched matrix and grades verdict stability across
four axes: **disjoint data-window segments** (the variance probe — trailing windows share the recent
tail and hide the swing), **friction** (0.30/0.70/1.0% RT), **per-regime** (BULL/BEAR/CHOP entry-
conditioned worst-week DD), and a **60/40 walk-forward** holdout. Grade = robust / fragile / unstable,
keyed off the *worst* plausible window + the spread, not the mean. SIM-only/read-only (verified: wrote
**no** verdicts, journal, or selector — only the report).

**Live result (2500 bars × 8 tokens), ranked stability-first:**

| Arm | Grade | segPass | ddMax | spread | worstReg | t/wk |
|---|:--:|--:|--:|--:|:--:|--:|
| **dual_momentum** | **ROBUST** | 100% | **11.5%** | **3.8%** | BEAR | 9.6 |
| rotation | ROBUST | 100% | 16.5% | 5.2% | BULL | 22.9 |
| momentum_fast | ROBUST | 100% | 17.4% | 5.9% | BULL | 21.6 |
| momentum_voltarget | ROBUST | 100% | 14.3% | 6.9% | BULL | 34.9 |
| mean_reversion | ROBUST | 100% | 15.9% | 7.6% | BULL | 22.8 |
| momentum | FRAGILE | 100% | 18.5% | 11.0% | BULL | 11.3 |
| **momentum_adaptive** *(locked default)* | **FRAGILE** | 100% | 18.5% | 11.0% | BULL | 11.3 |
| momentum_mafilter | FRAGILE | 100% | 23.7% | 15.2% | BULL | 10.7 |
| **breakout** | **UNSTABLE** | 80% | **31.7%** | **24.4%** | BULL | 24.5 |

**Findings:**
1. **`breakout` is UNSTABLE** — one of five disjoint segments fails (80% pass), ddMax 31.7%, a 24.4-pt
   spread and 2 verdict flips. The 14→31 swing was not a fluke; it's a fragile arm. Per-regime: BULL
   entry 31.7% vs BEAR 12.1% — it deploys heavy right after a breakout, then a reversal craters it.
2. **The locked default `momentum_adaptive` is only FRAGILE** (spread 11.0%, ddMax 18.5%) — its single
   PASS is wobblier than ideal. Not a problem (it's DQ-safe), but worth knowing.
3. **`dual_momentum` is the most ROBUST contest candidate** — ddMax 11.5%, spread 3.8%, tight across
   ALL regimes (11.1/11.5/11.5). Its basket cash-out keeps the worst-week DD low and *stable*. This is
   a concrete, evidence-backed input to contest-arm selection (forward check + sign-off still required).

**Verify:** `make stability` → report at `data/reports/strategy_stability.md`; `make stability
ARGS="--no-save"` prints only; offline smoke on a synthetic matrix grades without network.
`tests/test_strategy_stability.py` (9 tests) pins the grade thresholds, disjoint segmentation, the
stability-first ranking, and an offline end-to-end smoke.

## S6 — Contest-safety + forward runbook ✅

`tests/test_contest_safety.py` (4 tests) — the standing gate:
- the **campaign is read-only on the journal** (the rows it reads are byte-unchanged after a run) and
  **never writes the SIM selector**;
- **stability persists NO verdicts** (monkeypatching `verdicts.record` to raise proves it's never
  called) and writes only its report;
- the locked **`momentum_adaptive` weight path is bit-for-bit** the allocator's;
- a **LIVE dispatch ignores the SIM selector** — `strategy_select.json=dual_momentum` resolves
  `dual_momentum` in SIM but `momentum_adaptive` in LIVE. A dashboard click can never reach the contest.

Added the **real forward-accrual runbook** to [strategy_campaign.md](strategy_campaign.md) §7.1
(reset → `STRATEGY_NAME=<arm>` → daily SIM ticks → `forward_promote`), noting `make stability` is the
cheap way to shortlist *which* arm to spend the wall-clock forward window on.

## S7 — Coverage + full gate ✅

Extended `make coverage` to include the campaign + stability suites. Full gate:
- **`pytest` → 1357 passed**, 9 skipped (integration) — +42 tests vs the campaign baseline.
- `ruff` clean across all new/changed files.
- `make validate_allocator` → unchanged **PASS** (worst-week DD 18.1% within the 25% ceiling, 15.4
  t/wk) — the locked default is untouched; registry bit-for-bit test green.
- Coverage: `strategy_campaign` 81%, `strategy_stability` 87%, `forward_promote` 71% (uncovered = CLI glue).

---

## S8 — Parameter-sensitivity sweep ✅ (turning the finding into an improvement)

Built [`scripts/strategy_sweep.py`](../scripts/strategy_sweep.py) + `make sweep_arms`: for each tunable
arm, grid-search its key params and re-grade every config through the stability harness
(`probe_strategy`, extracted in a behavior-preserving refactor). Ranked **stability-first** (grade →
tightest spread), **not** best-DD — with the 60/40 walk-forward overfit delta shown. READ-ONLY
recommender (persists no verdicts, changes no arm). Report: `data/reports/strategy_sweep.md`.

**Live findings (2500 bars):**

1. **`breakout`: UNSTABLE → ROBUST.** The default (entry20/exit10/**rb6**) is UNSTABLE (ddMax 31.7%,
   spread 24.4%). **`entry20/exit5/rb3` → ROBUST** (ddMax **13.9%**, spread **4.8%**, overfitΔ −4.5%).
   Every `exit5/rb3` config is ROBUST regardless of entry length — a **shorter exit channel (5-bar low)
   + 12h rebalance** flattens losers before the reversal craters the book, exactly as the arm's
   docstring predicted ("the faster exit replaces the absent AMM stop"). The 10-bar exit was too slow.
   *(Caveat: 33.7 t/wk — higher live AMM friction; forward-validate.)*
2. **`mean_reversion`: ROBUST → tighter.** Default win20/z1.0/rb6 (15.9%/7.6%) → **win30/z1.0/rb6**
   (13.4%/**3.8%**), and z1.5 variants tighter still (win20/z1.5/rb6: ddMax 10.1%). A wider mean window /
   stricter z-threshold = more selective = lower, steadier DD.
3. **`momentum_fast`: already robust.** L60/rb3 ROBUST; L60/rb1 marginally tighter (spread 3.6%) but
   60 t/wk. No meaningful win — the registered default is sound.

**Overfit check:** nearly all overfitΔ are negative (holdout calmer than train) → these are robustness
wins, not curve-fits. None exceeds the 5-pt smell threshold materially on the recommended configs.

**Verify:** `make sweep_arms` (or `ARGS="--arm breakout"`); `tests/test_strategy_sweep.py` (9) pins
grids, ranking, verdict + overfit-smell, SIM-only. These are **candidates** — a config is promoted only
by a deliberate re-registration + forward validation + operator sign-off (no edge claim).

## Bottom line (for contest-arm selection)

The suite is hardened and every arm's mechanism + every campaign/forward invariant + contest-safety is
pinned. Two decision tools now exist:
- **`make stability`** grades trustworthiness: `dual_momentum` most **ROBUST** (ddMax 11.5%, spread
  3.8%), the locked `momentum_adaptive` only **FRAGILE** (spread 11%), `breakout` **UNSTABLE**.
- **`make sweep_arms`** turns a fragile arm into a robust one: `breakout` goes **UNSTABLE → ROBUST**
  with a 5-bar exit + 12h rebalance (entry20/exit5/rb3). The instability was a *parameter* problem, not
  a dead arm.

**Next moves** (still a long way to go): (1) shortlist `dual_momentum` (robust default) — run it forward
in SIM per §7.1 to earn a forward verdict; (2) consider re-registering `breakout`'s defaults to
exit5/rb3 (a deliberate edit + forward-validate); (3) optional: wire the stability/sweep grades into the
dashboard. Everything here is instrumentation that makes "improve and improve" measurable — no config is
contest-bound without a forward check + operator sign-off.

---

## Follow-ups — all three moves shipped (2026-06-13)

| # | Move | Result |
|---|---|---|
| 1 | **Re-register `breakout` → robust** | exit5/rb3 default. `breakout` now **ROBUST** (was UNSTABLE) and **survival ✅ 13.9% DD** (was ❌ 31.7%) — #2 in the campaign behind `dual_momentum`. |
| 2 | **Isolated `dual_momentum` forward track** | seeded into `data/forward/dual_momentum/`; production journal **byte-identical** (md5 unchanged). Accrues independently. |
| 3 | **Stability badge on the dashboard** | grade JSON sidecar → API → a 3rd badge (ROBUST/FRAGILE/UNSTABLE) on the selector. |

**Move 1 — breakout re-registered.** `breakout`'s default is now `entry_lb=20, exit_lb=5, rebal_bars=3`
(the sweep's ROBUST config); the 5-bar exit flattens losers before the reversal. `BNB_STRATEGY_05`
inherits it; the locked `momentum_adaptive` is untouched (`make validate_allocator` unchanged 18.1% DD).
Documented in [bnb_strategy_decision.md](bnb_strategy_decision.md) §9. Re-ran stability/sweep/campaign →
`breakout` ROBUST everywhere.

**Move 2 — isolated forward track.** `ALLOCATOR_DATA_DIR` ([settings.py](../src/ictbot/settings.py)
`_resolve_data_dir`) redirects an arm's whole data tree, so `make forward_track ARM=dual_momentum` runs
it in `data/forward/dual_momentum/` without clobbering the production SIM track the dashboard shows.
Seeded the first tick (verdict matures over ~1–2 weeks via cron — §7.1). Production journal proven
unchanged (md5).

**Move 3 — stability badge.** New [stability_grades.py](../src/ictbot/runtime/stability_grades.py)
(mirrors `verdicts.py`) is written by `make stability` and merged into `strategies_card` (reusing the
alias-inheritance pattern); `StrategyMenuItem` + `types.ts` gain a `stability` field; `StrategySelectPanel`
renders a third badge. Read-only display off the existing snapshot — no new endpoint, no control surface.

**Gate:** **1373 tests pass** (+7), ruff clean, `tsc --noEmit` 0, `make validate_allocator` unchanged
(locked default bit-for-bit), registry-equivalence green.

---

## Capstone — forward automation + contest-readiness rollup (2026-06-14)

Committed the whole body of work in 4 clean conventional commits (strategy · stability badge · docs ·
the dashboard cockpit UX), then added the capstone that ties the campaign together:

- **`make forward_track_all`** — ticks every challenger's isolated forward track in one shot
  (`FORWARD_ARMS=dual_momentum breakout`), cron-ready (12h). Seeded both; production journal proven
  byte-identical (md5 unchanged). The re-registered `breakout` now actively deploys (8 swaps).
- **`make readiness`** ([scripts/contest_readiness.py](../scripts/contest_readiness.py)) — fuses
  **stability + survival + forward** into ONE verdict per arm. Forward prefers the isolated track when
  present. Read-only; never auto-promotes (operator sign-off is the last step).

**Live rollup:** `momentum_adaptive` 🔒 INCUMBENT; every challenger ⏳ **IN PROGRESS** (survival ✅ +
stability ROBUST, blocked only on forward accrual) — ranked by DD with **`dual_momentum` (ROBUST,
11.5%)** on top, then `breakout` (ROBUST, 13.9% — the re-register shows through). Once the cron accrues
~2 weeks of forward span, the top challengers flip to ✅ READY (pending sign-off). `tests/test_contest_readiness.py`
(5) pins the four verdict states + isolated-track preference + SIM-only.

**Where it stands:** the decision loop is now fully instrumented end-to-end — **stability** (trust) →
**sweep** (fix) → **forward track** (evidence) → **readiness** (one verdict). The only thing between
here and a contest-arm decision is wall-clock forward accrual + an operator sign-off.

---

## Grid built — the playbook is complete (2026-06-14)

`grid` ([adapters/grid.py](../src/ictbot/strategy/adapters/grid.py)) was the last unbuilt playbook arm:
a **net-inventory grid** (buy lower-in-range / sell higher-in-range) with a **hard breakdown stop**,
expressed as a price-responsive target weight (no resting orders) reusing the Donchian channels.
Registered as `grid` / `BNB_STRATEGY_09`; it auto-flows through campaign / stability / sweep / readiness
(they iterate the registry) — **no UI code** (deprioritized).

**Measured: FRAGILE** — survival ✅ but worst-week DD **21.6%** (second-riskiest, just inside the 25%
rail) at **53.7 t/wk**. The hard stop is what keeps it DQ-safe (without it → UNSTABLE); the sweep finds
no ROBUST grid config. Readiness: ⏳ IN PROGRESS (survival ✅ + FRAGILE, forward accruing). Honest
result — grid completes the playbook as a high-turnover capability arm, **not a contest candidate**.
`tests/test_adapter_grid.py` pins the inventory monotonicity + the breakdown stop + the cap. Decision
record §10. **`make forward_track_all` is scheduled** (CronCreate, durable, ~12h) to accrue the top
challengers' forward evidence over the first week (auto-expires after 7 days; the OS cron in §7.1 is the
full-window path).

---

## Playbook wired to the implementation + PnL/win-rate scoreboard (2026-06-14)

The research playbook ([strategy_playbook.md](strategy_playbook.md)) was pure narrative — nothing tied
its Top-10 families to the registered arms, and validation was risk-first only (PnL + win-rate were
computed by `portfolio_replay.evaluate` then thrown away). **No new strategies** — this round is audit +
wiring + measurement, framed **gate + scoreboard**: risk-first survival stays the hard pass/fail GATE;
PnL + win-rate is a labelled SCOREBOARD over the survivors, never an edge claim (the VALIDATION UPDATE
banner + §11 in the playbook).

**Built:** [`runtime/performance.py`](../src/ictbot/runtime/performance.py) — `backtest_perf` (surfaces
the already-computed `total_return` + WINDOW win-rate `pct_up`) and `daily_pnl`/`forward_perf` (a Python
port of `web/src/lib/pnl.ts` → live DAY win-rate, so report and dashboard reconcile). The campaign now
persists a **`perf`** verdict kind and renders a **PnL / win-rate scoreboard**;
[`playbook_status.py`](../scripts/playbook_status.py) (`make playbook`) splices a per-arm **§11 status
matrix** into the playbook (family lineage + survival + stability + forward + scoreboard) and
`tests/test_playbook_parity.py` pins playbook↔registry coverage both ways. Step-by-step runbook:
[strategy_validation_runbook.md](strategy_validation_runbook.md); one-command `make validate_all`.

**Measured (honest):** at the binding 0.70% friction every long-only arm is **net-negative over the full
backtest** (−37.6% `momentum_voltarget` best → −69.1% `breakout`), with WINDOW win-rates 16–33% — the
"no long-only edge" thesis made visible. The scoreboard ranks least-bad among survivors; it is decision
support, not alpha. Forward PnL / day-win-rate fills in over wall-clock days (`momentum_adaptive` +0.43%
/ 100% 1-of-1d so far). +17 tests (`test_performance` 7, `test_playbook_parity` 9, campaign scoreboard
1); locked `momentum_adaptive` untouched (bit-for-bit; 18.4% DD this window = live-data drift, not code).

---

## Decision loop finished — validation gaps closed, decision-ready (2026-06-14)

The strategy *building* was already done; this closed the remaining **decision-loop + validation** gaps
(no new strategies, no UI):

- **Per-arm coverage.** Every registered arm now has a dedicated test — new `test_adapter_momentum`,
  `test_adapter_momentum_adaptive` (pins the **locked incumbent** bit-for-bit + the regime-cap scaling),
  `test_adapter_momentum_fast`, and composed-overlay integration tests in `test_overlays` (the registered
  `momentum_voltarget` / `momentum_mafilter` obey the long-only de-risk invariant end-to-end).
- **Decision-loop recommendation + honesty.** `scripts/contest_readiness.py` gained `recommend_arm` (one
  line: `PROMOTE-CANDIDATE: <arm>` or `STAY INCUMBENT` — strict gate: READY **+** ROBUST **+** non-vacuous
  forward, risk-first; never auto-promotes) and `deploy_summary` (labels cash-vacuous tracks `⏳ accruing
  (cash — deploy_cap≈0)` — the eligibility gate is unchanged). `FORWARD_ARMS` widened 2→**8 challengers**.
  Current verdict: **STAY INCUMBENT** (every challenger forward-accruing; `breakout` NOT_READY).
- **`breakout` resolved → research-only.** It grades **UNSTABLE** on the current window (31.2% DD) and the
  sweep finds **no ROBUST config** (best is FRAGILE with a −14.3% overfit smell). Flagged like `grid` —
  not a contest candidate; not re-registered (would be curve-fitting). Decision record §12. The
  recommendation gate already excludes it (only ROBUST arms surface).
- **Live-promotion proven safe.** New tests show a non-default arm (`dual_momentum`) promoted via
  `STRATEGY_NAME` resolves on LIVE and runs end-to-end through the **strategy-agnostic** tick→broker; LIVE
  ignores the SIM selector (contest-safety). Added `--preflight-only` (validate creds + resolved strategy,
  **exit before any swap**) to arm live safely.

**+29 tests (1399 → 1428)**; ruff clean; `validate_allocator` **18.4% DD ✅ bit-for-bit** (locked path
untouched). Full procedure: [strategy_validation_runbook.md](strategy_validation_runbook.md) /
`make validate_all`.

---

## Fully functional: sim-test every arm · trade all 8 tokens · CMC measured (2026-06-14)

Three independent topics, all additive/reversible — locked default + live CMC config untouched.

- **Sim-test all strategies** (`make sim_test_all` + [`scripts/sim_test_all.py`](../scripts/sim_test_all.py)):
  validates every arm's isolated SIM journal + state round-trip (schema, NAV, weights ≤ 1, universe-only
  tokens, n_swaps↔tx, ledger) and surfaces breadth → `data/reports/sim_test_all.md`. Result: **9 OK, 0
  ERROR** (breakout touches all 8 tokens, grid 7, rotation 3, momentum-family 2; dual_momentum/
  mean_reversion validly cash; incumbent validated against the production journal = BNB+CAKE), 1 EMPTY
  (base momentum, no track). The pytest loops every arm offline — the missing coverage, now closed.
- **Trade all 8 tokens — floor rotation** (option A): the contest ≥1-trade/day nudge now **round-robins
  the universe** (`trade_floor_rotate=True`, contest-only, ~0-NAV) so every token is touched over the
  week — WITHOUT changing the momentum allocation (locked default stays `top_k=2`). `_floor_picker` +
  `floor_cursor` in state; legacy largest-holding nudge preserved when off. Tests prove 8 daily nudges
  touch all 8.
- **CMC measure-first** (`make cmc_check` + `make ab_regime`): the diagnostic revealed CMC is **already
  fully lit in the live config** (key set, F&G/intel/TA/skill LIVE per the journal) — only the *code
  defaults* are off. The A/B **confirms** the enabled subset is right: `enhanced`+TA improve
  risk-penalized return, DQ-safe (`enhanced+ta` +4.2pts), while over-stacking (`full_cmc`,
  tilt/ranking-alone) is worse. Recorded in [cmc_enablement.md](cmc_enablement.md); **no live flags
  flipped**.

**+18 tests (1428 → 1446)**; ruff clean; `validate_allocator` **15.6% DD ✅ bit-for-bit** (calmer
data-window; code untouched). New: `make sim_test_all` · `make cmc_check`.

---

## MCP wiring proven + skills paired (2026-06-14)

User reported "the MCPs are not wired up." A three-way trace + a live probe proved the **opposite**: the
CMC MCP **is** wired ([`cmc_agent_hub.py`](../src/ictbot/data/cmc_agent_hub.py) `_rpc` → HTTP JSON-RPC to
`mcp.coinmarketcap.com/mcp`) and the skills consume it — the journal shows `ta_source: "cmc+skill"`,
`cmc_intel_used: true`, `cmc_skill.tools_used`. It only *looked* unwired because my `cmc_check` rendered
the MCP row as the raw flag (`ON`) instead of a live probe.

- **`make mcp_check`** (`scripts/mcp_check.py`): live `tools/list` + a sample `tools/call` + a canonical
  **SKILL↔tool pairing map** → headline **"MCP LIVE ✅ — 12/12 tools, 10 skills PAIRED"** +
  `data/reports/mcp_status.md`. Added `cmc_agent_hub.live_tools()` + `ping()` (the read-only health
  surface). `make probe_agent_hub` exposed the deeper one-shot probe.
- **`cmc_check` fixed:** the MCP row is now live-verified (`✅ LIVE — 12/12 tools`), not the bare flag.
- **Honest scope** ([docs/mcp_wiring.md](mcp_wiring.md)): it's CMC's HTTP-JSON-RPC branded "MCP" (no `mcp`
  SDK/stdio — works, not the literal protocol); the **Crypto.com** claude.ai MCP is a session tool the
  standalone cron bot can't reach (not wired, by design); skills fall back to local compute if the MCP is
  down (never a hard failure).

**+11 tests (1446 → 1457)**; ruff clean; `validate_allocator` **15.6% DD ✅ bit-for-bit** (no allocator
touch — additive health surface only). No live flags flipped.

---

## Audit-driven hardening: LIVE-path safety + broker coverage + lint (2026-06-14)

A 42-agent adversarial "what's left" audit (12 REAL-OPEN, 5 wall-clock, 18 dismissed) surfaced a handful
of genuine gaps — all LIVE-arming hardening + hygiene, locked default untouched, SIM default unaffected:

- **Kill switch in the allocator live path** — `_live_preflight()` now refuses when
  `kill_switch.is_engaged()`, so a dashboard kill press halts a running `--loop` live process (it
  previously couldn't — `enable_live_trading` is import-cached; the sentinel is the only in-process halt).
- **Partial-flatten signal** — the 4 DD_HALT/PROFIT_LOCK journal sites now record `flattened_ok` vs
  `flattened_attempted` + `flatten_partial` + `flatten_errors`, so a failed sell leg (residual exposure)
  is no longer masked as "all flat". **`--resume`** blocks on a prior partial flatten unless `--force`.
- **Live-broker coverage** — `CliTwakClient._run()` retry/backoff/transient-classification + `swap()`
  silent-degradation branches (price-raises→price=0, non-numeric fee→0, run-failure→ok=False) now tested
  (the live-money path had none).
- **Lint/CI** — CI ran *unpinned* ruff and was RED (37 `ruff check` errors in pre-existing files); fixed
  via targeted `--fix` + manual B905, restored the `analyzer.append_signal` re-export, pinned CI to
  `ruff==0.15.17`. The repo-wide `ruff format` drift (~158 files incl. the locked allocator) is a
  deliberate one-time adoption deferred from this near-submission set; CI format-check kept informational.
- **Doc drift** — committed the auto-gen campaign/playbook regen so the docs' stability grades match the
  committed seed data (breakout UNSTABLE / rotation FRAGILE).

**Deliberately NOT done** (verified out-of-scope/wall-clock): the Jun 20–21 operator arming (`.env` flip +
live crons), forward maturation, the wallet-password-in-argv defense-in-depth (needs `twak swap --help`
CLI-contract confirmation), CMC freshness guard (OFF-by-default arms).

**1457 → 1471 tests**; `ruff check src tests` ✅ green; `validate_allocator` ✅ bit-for-bit.
