# Strategy — Regime-Adaptive Allocator · live arm: mean-reversion (BNB Hack, Track 1)

> Judge-facing strategy explanation. The full decision record (why every alternative was
> rejected) is [bnb_strategy_decision.md](bnb_strategy_decision.md); the A/B evidence is
> [cmc_pnl_ab.md](cmc_pnl_ab.md). This document is the standalone read.

## 1. Thesis in one paragraph

There is **no positive-edge long-only TA strategy** on the 8-token contest universe at
realistic DEX friction — we proved it five independent ways (§2). A 7-day result on eight
liquid majors is variance around break-even, gated by a hard 30%-drawdown disqualifier. So
we do not optimize for alpha (there is none to capture); we optimize for the contest's
actual scoring function: **survival + participation + craft.** The agent is built to be
nearly impossible to disqualify, to participate in upside when the live week trends, to
clear the 7-trade floor, and to showcase the CMC · TWAK · BNB-SDK toolchain end to end.

## 2. The negative-edge audit (why this strategy, not alpha)

Confirmed five independent ways, all at contest-realistic friction
([bnb_strategy_decision.md](bnb_strategy_decision.md) §1; reproduce with `make validate_trend`):

| Test | Result | Where |
|---|---|---|
| ICT POI/MSS/FVG entry | negative out-of-sample expectancy | [findings.md](findings.md) §13–§15 |
| Trend pullback (selective, 4h) | only ETH holds; ~0.6 trades/wk (too rare) | `scripts/validate_trend.py` |
| Trend loosened / 1h | enough trades, **edge gone** (all TEST negative) | probe |
| Friction sweep 0.10%→0.70% | net basket expectancy **negative even at 0.10%** | probe |
| Portfolio search, **2,338** rolling 7-day windows | **every** strategy median weekly return ≤ 0 | `scripts/validate_allocator.py` |

The honest conclusion shaped every design choice below.

## 3. What ships — a regime-adaptive allocator (live arm: mean-reversion)

The engine is **pluggable**: an arm selects names over the 8 tokens (`BNB, ETH, CAKE, LINK, UNI, AVAX,
DOT, DOGE`, `CONTEST_TOKENS`), the held tokens are **inverse-volatility** sized (30-bar), and a
**regime-adaptive cap** sets how much NAV is deployed — the same wrapper for every arm. Each rebalance
(≈daily):

1. **Select** per the live arm's rule. The **live arm is mean-reversion**
   ([mean_reversion.py](../src/ictbot/strategy/adapters/mean_reversion.py), pinned via `.env
   STRATEGY_NAME`): buy oversold names trading **>1σ below their rolling mean** (lower Bollinger band) —
   a contrarian, high-activity stance that trades every rebalance. *(The registered incumbent
   `momentum_adaptive` ([momentum_allocator.py](../src/ictbot/strategy/momentum_allocator.py)) instead
   ranks by 120-bar return and holds the top-2; it stays the bit-for-bit fallback.)*
2. **Size** the held tokens by **inverse volatility** (30-bar).
3. **Deploy adaptively** — the deployment cap is *not* a frozen number. It scales with a live risk-on
   score (breadth + index trend + volatility + CMC Fear & Greed) into the participatory band
   **[0.40, 0.85]**. Code: [regime_score.py](../src/ictbot/strategy/regime_score.py).
4. **Rebalance** the book toward those weights via **TWAK spot swaps** (sells before buys).

No SL/TP brackets — an AMM swap has no native stop. Risk control is the adaptive cap + diversification +
a hard **drawdown halt** (NAV vs high-water mark → flatten + stop) + an intraday dd-watch.

**Why mean-reversion is the live arm:** across every arm the edge is ~zero (§2), so we pin the one that
best serves the scoring function — mean-reversion is the most **DQ-safe** (13.2% worst-week DD) and
**active** (~26 trades/wk) of the registered arms (survival-passed, DQ-safe; forward-validation in
progress). Switching arms is **operator-gated** (the auto-selector only recommends — never auto-switches
LIVE).

## 4. Why adaptive, not a frozen cap

A backtest only describes the past; freezing a cap to one (net-bearish) historical window is
the hindsight trap. The contest week's regime is unknown, so deployment **reacts to live
signals**. Behaviour, verified across the 8-token history (this is *behaviour we control*, not
a return prediction — there is no edge to predict the next week):

| Regime | Mean deploy cap |
|---|---|
| BULL | **0.78** — deploys, captures upside |
| BEAR | **0.45** — defends into cash |
| CHOP | **0.52** — volatility-cut |

**Adaptive vs a static high cap** (rolling-7-day backtest, 0.70% round-trip friction, on the momentum
incumbent): the adaptive cap keeps most of the upside of a static 0.85 cap (**p95 +10.4% vs +12.3%**) at
materially lower worst-week drawdown (**17.6% vs 22.3%**) — better risk-adjusted exposure, DQ-safe
(< 30%). The cap is **arm-agnostic** — it wraps any arm. The **live arm (mean-reversion)** is
survival-validated at **worst-week DD 13.2%, ~26 trades/wk, DQ-safe**
([strategy_gates.json](../data/reports/strategy_gates.json)); the momentum incumbent's vintage is
**17.3% DD, ~15.4 trades/wk** ([bnb_strategy_decision.md](bnb_strategy_decision.md) §7).
*Anti-overfit:* the regime logic is a simple, principled linear map — not grid-searched on
the sample — so we don't re-introduce the bias we're avoiding.

## 5. CMC data levers (A/B-validated)

The engine and candles are held constant; only the CMC lever changes — judged on
risk-penalized return (`total_return − worst_week_dd`) over **2,298** rolling 7-day windows
at 0.70% friction on a down-leaning 14-month sample, so the question is which lever *loses
less / draws down less* ([cmc_pnl_ab.md](cmc_pnl_ab.md)):

| Lever | Δscore | Δ worst-week DD | Verdict |
|---|---:|---:|---|
| Enhanced regime (CMC macro: BTC-dominance, total-mktcap, F&G-momentum → cap) | +8.6 pts | −1.0 pt | ON |
| `ta_cap` (CMC pre-computed TA → cap) | +12.4 pts | −1.0 pt | ON |
| `ta_rank` (CMC TA → token ranking) | +5.5 pts | ±0 | wired into the LIVE ranking path, A/B-gated (`ALLOC_TA_ENABLED`) |
| **`enhanced+ta`** (macro + TA in the cap) | **+12.7 pts** | **−1.3 pts** | **best arm** |
| Over-stacked `full_cmc` / bare tilt / multi-TF ranking | negative | — | kept OFF |

LIVE reads CMC's authoritative pre-computed TA via the Agent Hub MCP — same signal, compute
offloaded to CMC. Promotion discipline is SIM-first: levers run on the SIM track and are
forward-validated before the contest entry adopts them. *Data provided by CoinMarketCap.*

## 6. Risk controls & DQ-safety

Both contest gates have a strategy-level **and** a mechanical-failure-level defense
([bnb_strategy_decision.md](bnb_strategy_decision.md) §7):

| Gate | Strategy-level | Mechanical-level |
|---|---|---|
| 30% max drawdown (DQ) | adaptive cap defends to 0.40/cash in BEAR; inverse-vol sizing; live arm (mean-reversion) worst-week **13.2%** (incumbent momentum_adaptive 17.3%) | hard **drawdown halt** vs high-water mark → emergency flatten + stop; atomic state writes so a crash can't corrupt the HWM |
| ≥7 trades/week **and ≥1/day** | live arm ~**26 trades/wk** natural cadence (incumbent ~15.4) | **trade-floor tracker** (weekly) + **`--ensure-daily-floor`** (daily) → bounded ~0-NAV round-trip nudges when behind pace |

**Campaign mode (forward sim track, 2026-06-13).** A tighter operator overlay for the run-up
to submission ([decision record §8](bnb_strategy_decision.md)): a **10% drawdown halt** (vs the
30% DQ line) plus a **profit-lock ratchet** — arm at +5%, trail 3% off the peak, bank at +10% —
that flattens to USDT and stops, so a lucky spike is *kept* rather than round-tripped. Both the
scheduled tick and a 15-min intraday watcher enforce it. Honest: the sweep
([campaign_sweep.json](../data/journal/campaign_sweep.json)) puts the +5% shot at ~21% across
full history but only ~9% in the recent choppy regime, and a 25% halt buys no extra upside — so
10% is the rail. The committed defaults remain the validated baseline; the campaign is `.env`-only.

Plus: failed swaps return `ok=False` and are journaled (one bad swap can't crash a
rebalance); tick-skip guards on invalid/zero price, zero NAV, stale candles (>12h); per-mode
`flock` idempotency (cron overlap can't double-execute); live preflight + on-chain
`RECON_DRIFT` reconciliation; a kill switch. Most hackathon agents die to a crashed cron, not
a bad signal — this one is built not to.

**Compliance:** spot swaps only — no token launches, no fundraising, no airdrop activity
during the event window (explicit contest DQ rules).

## 7. Validation methodology

A backtest cannot validate a forward week, so the backtest is a **cross-regime sanity check**,
and the real evidence is a **daily paper run on genuinely unseen data** from build-finish to
contest open (`make forward_report`). The deployment band is a runtime override
(`ALLOC_CAP_FLOOR`/`ALLOC_CAP_CEILING`, `ALLOC_ADAPTIVE`) — dial it down near 06-22 if the
week looks volatile.

## 8. Rejected alternatives (no path back)

- **Perps / leverage** — OUT by organizer ruling (BNB Chain, 2026-06): only transactions
  through the **TWAK swap interface** count toward contest P&L, so a perp leg contributes
  zero regardless of how it's built. (Independently, TWAK cannot sign perps for agents.) A
  perp leg was prototyped and removed once the ruling landed.
- **The ICT entry stack** — negative OOS expectancy ([findings.md](findings.md)); retained only as
  the legacy upstream engine (its standalone docs have been retired).
- **Per-token trend signals** — no basket edge even at 0.10% friction (§2).

## 9. Reproduce it

```bash
make validate_trend                     # the negative-edge audit that led here
make validate_allocator                 # rolling-7-day proof: DQ-safe + deployment-by-regime
make run_allocator                      # one adaptive rebalance tick (sim; journals to data/journal/)
make forward_report                     # forward paper track record on unseen data
make ab_regime                          # the CMC lever A/B (regenerates cmc_pnl_ab.md)
```

Sim runs need no keys. Live execution needs the `twak` CLI + `twak setup` +
`ENABLE_LIVE_TRADING=true` — boot guards refuse anything less.
