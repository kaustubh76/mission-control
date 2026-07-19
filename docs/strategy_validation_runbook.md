# Strategy validation runbook — step-by-step

> **How to validate the whole project, in order.** Companion to
> [strategy_playbook.md](strategy_playbook.md) (what each strategy IS) and
> [strategy_campaign.md](strategy_campaign.md) (the live status matrix). This is the ORDERED
> procedure that proves every registered arm is implemented, tested, survival-gated, stability-graded,
> forward-tracked, and wired back to its playbook family — and surfaces the **PnL + win-rate**
> scoreboard for picking the contest arm.

## The principle — GATE then SCOREBOARD

There is **no proven long-only edge** on the 8-token universe ([bnb_strategy_decision.md](bnb_strategy_decision.md) §1).
So validation has two layers and they are **not** the same thing:

- **GATE (pass/fail rail).** Risk-first survival: worst rolling-7-day DD **< 25%** AND **≥ 7 trades/wk**
  at the binding 0.70% friction. An arm must clear this to be ranked at all. The locked
  `momentum_adaptive` is the default and stays **bit-for-bit** unchanged.
- **SCOREBOARD (performance, among survivors).** Per-arm **PnL** (backtest total-return + forward net)
  and **win-rate**, ranked. This is the operator's decision aid — **never an edge claim**. A backtest
  total-return ranking over the long trending sample is dominated by **regime luck**; read the
  contest-length window win-rate and the live day win-rate as the decision-relevant numbers.

**Two distinct win-rates — keep them labelled:**
- **Window win-rate** (backtest) = share of rolling 7-day (contest-length) windows that finish positive (`pct_up`).
- **Day win-rate** (forward) = live up-days / decided-days on the EOD NAV series — a Python port of
  `web/src/lib/pnl.ts`, so the dashboard % and the report reconcile.

## One command

```bash
make validate_all
```

Runs the whole chain below (offline tests → locked-default check → campaign → stability → readiness →
playbook). **Online** (campaign/stability fetch the universe) — minutes, not a CI step. The offline
always-green subset is `make test`.

---

## Step 0 — Invariants & parity (offline, always-green)

```bash
make test            # full pytest suite
```

Asserts, with **no network** (conftest forbids it):
- **Long-only-spot invariants** — every registered arm's weights are in `[0, 1]` and never sum > 1
  (no shorting, never over-deployed) — `tests/test_strategy_registry.py`.
- **Alias bit-for-bit** — every `BNB_STRATEGY_0X` delegates byte-identically to its target — `tests/test_strategy_aliases.py`.
- **Playbook ↔ registry parity** — every Top-10 family maps to a registered arm and every registered
  arm has a playbook home — `tests/test_playbook_parity.py`.
- **PnL/win-rate parity** — the Python scoreboard math matches the dashboard `pnl.ts` definition —
  `tests/test_performance.py`.
- **Campaign/stability are read-only** against the live world — `tests/test_contest_safety.py`.

**Pass:** all green. This is the gate that runs on every change.

## Step 1 — Locked default unchanged

```bash
make validate_allocator
```

Replays the locked `momentum_adaptive` over all rolling 7-day windows on the 8-token universe.
**Pass:** worst-week DD and return distribution **identical** to the committed record (bit-for-bit) —
proves the validation work did not perturb the contest default. SIM-only.

## Step 2 — Backtest survival GATE + PnL/win-rate SCOREBOARD

```bash
make campaign                              # full run, persist verdicts + docs
make campaign ARGS="--forward-min-days 14" # rigorous forward window (default is the 5d compressed)
```

For **every real arm** (registry minus the bit-for-bit aliases): runs the survival gate and writes the
**PnL/win-rate scoreboard**.
- **Persists** `data/reports/strategy_gates.json` — `survival`, `forward`, and the new **`perf`**
  (backtest total-return + window win-rate) per arm.
- **Rewrites** the guardian matrix in [strategy_campaign.md](strategy_campaign.md) (risk-first) and the
  comparison report `data/reports/strategy_campaign.md` — which now carries the
  **## PnL / win-rate scoreboard** section, ranked by performance among survivors.

**Pass:** every arm either clears the 25% DD rail or is honestly flagged ❌ (a fail is a valid result —
it just means "not contest-eligible," not a bug).

## Step 3 — Stability grades (which PASS can I trust?)

```bash
make stability                       # all arms
make stability ARGS="--arm breakout" # one arm
```

Grades each arm **ROBUST / FRAGILE / UNSTABLE** across disjoint data-window segments, friction levels,
per-regime DD, and a 60/40 walk-forward holdout — because the campaign's single worst-week DD is noisy.
Writes `data/reports/strategy_stability.md` + `strategy_stability.json`. **Pass:** no contest candidate
is UNSTABLE; prefer ROBUST.

## Step 4 — Parameter sweep (tunable arms)

```bash
make sweep_arms ARGS="--arm breakout"
```

Grid-searches a tunable arm's params, re-grades every config through the stability harness, and reports
the most-robust config vs the default (`data/reports/strategy_sweep.md`). Re-registering a default is a
**deliberate, forward-validated** edit — never automatic.

## Step 5 — Forward accrual (wall-clock-bound)

```bash
make forward_track_all     # tick the isolated per-arm SIM tracks (cron every 12h)
```

Accrues a real forward track per challenger in its **isolated** data tree (`data/forward/<arm>/`),
never touching the production journal. `FORWARD_ARMS` covers all **8 challengers**. The forward gate
needs **≥10 ticks AND ≥5-day span** (compressed) to evaluate; until then it reads **⏳ accruing**, and the
forward **net % / day win-rate** fill in over calendar days. This is the only step that **cannot be
compressed**. A track that sits **cash-vacuous** (deploy_cap≈0 in a risk-off regime → 100% USDT → flat
NAV) is labeled `⏳ accruing (cash — deploy_cap≈0)` in Step 6 and never counts as real evidence (the gate
already rejects it — 0 swaps < the 7 t/wk floor).

## Step 6 — Contest-readiness rollup + recommendation

```bash
make readiness
```

Fuses stability + survival + forward into **ONE** verdict per arm (`data/reports/contest_readiness.md`):
**✅ READY** (all gates cleared, pending sign-off) · **⏳ IN PROGRESS** (forward still accruing) ·
**❌ NOT READY** (survival ❌ or stability UNSTABLE) · **🔒 INCUMBENT** (the locked default), and emits a
single **Recommendation** line — `PROMOTE-CANDIDATE: <arm>` or `STAY INCUMBENT`. A promotion candidate
must STRICTLY clear every gate: READY **AND** stability **ROBUST** (stricter than READY's floor) **AND** a
**non-vacuous** (deployed, non-cash) forward track, ranked risk-first by lowest worst-week DD. **Never
auto-promotes** — advisory only; the flip stays operator sign-off.

## Step 7 — Playbook §11 parity + status

```bash
make playbook
```

Splices the **§11 Implementation & validation status** matrix into [strategy_playbook.md](strategy_playbook.md):
one row per arm — its playbook family lineage + survival + stability + forward + the PnL/win-rate
scoreboard. Proves the research doc and the code agree (parity enforced in Step 0). **Pass:** every
Top-10 family shows its arm with current validation status.

## Step 8 — Functional checks (sim-test all · CMC status)

```bash
make sim_test_all   # tick every arm in SIM, then validate journal + state read/write per arm
make cmc_check      # what CMC data/skills are LIVE vs degraded vs flag-off in the current config
```

- **`sim_test_all`** (`data/reports/sim_test_all.md`): every arm's SIM journal is schema-valid, NAV
  positive, weights ≤ 1, universe-only tokens, n_swaps↔tx, state ledger round-trips — and the **distinct
  tokens** each arm trades (breakout/grid touch the universe; the momentum family touches 2 — `top_k=2`,
  by design). **Pass:** no arm ERRORs.
- **`cmc_check`** (`data/reports/cmc_status.md`): each CMC source/skill → LIVE / ON / FLAG-OFF / DEGRADED.
  Measure the backtestable levers' PnL with `make ab_regime`; enable path in
  [cmc_enablement.md](cmc_enablement.md) (enable → SIM-validate → sign-off). **Read-only — no flags flip.**
- **Touch all 8 tokens:** the contest ≥1-trade/day floor **rotates** the universe (`TRADE_FLOOR_ROTATE`,
  default on) so every token is traded over the week without changing the momentum allocation.

---

## Where to read PnL + win-rate (the headline)

| View | Command / file | What it shows |
|---|---|---|
| **Scoreboard** (ranked) | `make campaign` → `data/reports/strategy_campaign.md` | Backtest total-return + window win-rate + forward net % + day win-rate, ranked among survivors |
| **Per-arm in context** | `make playbook` → [strategy_playbook.md](strategy_playbook.md) §11 | Each arm's PnL/win-rate beside its survival/stability/forward + playbook family |
| **Persisted** | `data/reports/strategy_gates.json` → `perf` | The backtest total-return + window win-rate, machine-readable |
| **Live (single book)** | dashboard P&L card / `web/src/lib/pnl.ts` | The deployed book's net PnL + day win-rate (the definition the Python port mirrors) |

## Promotion to LIVE — operator sign-off only

No step here promotes anything. The contest arm is chosen by a human setting `STRATEGY_NAME` +
`ENABLE_LIVE_TRADING`. READY means "every automated gate is cleared," not "deploy." The locked
`momentum_adaptive` stays the default until a deliberate operator decision changes it. The full
arm-it/disarm-it procedure is the single operator checklist:
**[live_arming_runbook.md](live_arming_runbook.md)** (this section is the pre-arm gate that precedes it).

**Arm the live setup safely (no swap):**

```bash
STRATEGY_NAME=<arm> python scripts/run_allocator.py --mode live --preflight-only
```

Validates creds + `ENABLE_LIVE_TRADING` + the resolved strategy, then **exits before any broker is built
or any swap is signed** — `OK` (rc 0) or `NOT ready` (rc 2). The LIVE dispatch is **strategy-agnostic**:
`STRATEGY_NAME=<arm>` resolves to that arm (LIVE never reads the SIM dashboard selector — contest-safety),
`registry.get` runs it, and the same `TwakSpotBroker.rebalance` consumes any arm's target weights — proven
end-to-end for a non-default arm by `tests/test_run_allocator_hardening.py`. Then drop `--preflight-only`
to go live.
