# BNB Hack — Strategy Decision Record (locked)

**Status: LOCKED + LIVE.** The contest agent is a **long-only spot regime-adaptive allocator** (live
arm: **mean-reversion**; `momentum_adaptive` is the registered incumbent/fallback), executed entirely
through **TWAK** (sole signer) on BSC. This document is the audit trail: it records *why* every
alternative was rejected and *what* exactly ships, so there is no path back to a superseded approach.

> **🔵 LIVE-ARM UPDATE (2026-06-20):** the live arm is now **mean-reversion**
> (`.env STRATEGY_NAME=mean_reversion`) — selected as the most DQ-safe + active registered arm
> (survival-passed: **13.2% worst-week DD, ~26 trades/wk, DQ-safe**; forward-validation in progress).
> `momentum_adaptive` remains the registered incumbent/fallback. The engine, the regime-adaptive cap,
> the CMC-native data path, and TWAK execution are **unchanged** — only the selecting arm differs. The
> "momentum allocator" wording below is the prior framing, kept as decision history; the current,
> canonical framing is in **`README.md`** / **`ARCHITECTURE.md`**.

> **🟢 LIVE ON-CHAIN (2026-06-08):** all three pillars proven with real txs. ERC-8004 identity
> minted (**agentId 1313**, gasless via MegaFuel); **registered for the contest** (`twak compete`,
> participant `0xE8A3…6215`); and the **autonomous agent traded live** — read CMC (F&G 15) → active
> regime-adaptive decision → executed via TWAK (native-gas swaps) → 42% into BNB+CAKE, with a
> natural-language rationale. Not a manual swap: the *strategy* drove it. Active stance re-validated
> DQ-safe (worst-week DD **17.3%** « 30%, **15.4 trades/wk** — the locked current vintage; an
> earlier 06-08 read showed 16.8%).
>
> **⚠ CORRECTION (2026-06-12):** the implementation audit found the 06-08 mint went to a wallet
> whose key was never pinned — on-chain `ownerOf(1313)` is an unrelated address, so that identity
> could never heartbeat. The **live identity is agentId 133085**, re-minted 2026-06-12 from the
> now-pinned identity wallet `0xEb7b…9655` (key persisted via `make remint_identity ARGS="--pin-key"`),
> with the trading wallet declared in its metadata and a **real on-chain heartbeat firing every
> allocator tick**. Proof: `data/compete/identity_mint_2026-06-12.json`. The contest registration of
> `0xE8A3…6215` was unaffected (re-verified `isRegistered=true` on-chain, 2026-06-12).

- **Contest:** BNB Hack: AI Trading Agent Edition. 7-day live window **2026-06-22 → 06-28**
  (deadline 2026-06-21 17:30 UTC). Judged on **total return**, **min 7 trades**, **hard 30%
  drawdown = disqualification** (team target ≤15%). On-chain BSC, simulated costs.
- **Universe (8):** `BNB, ETH, CAKE, LINK, UNI, AVAX, DOT, DOGE`.
- **Reproduce everything below:** `make validate_allocator` (rolling-7-day proof) and
  `make validate_trend` (the negative-edge audit that led here).

> **🟣 CMC-NATIVE CUTOVER (2026-06-15) — `momentum_cmc`, SIM-active / LIVE-ready.** The contest arm now
> decides and sizes **entirely on CoinMarketCap data, zero CEX**: selection on CMC's own 4h candles
> (the `cmc_stream.py` WebSocket feed + cold-start CMC-daily seed), sizing/cap on CMC Fear&Greed + CMC
> MCP technicals (`ta_rank`, A/B-proven) + the Skills-Marketplace market-overview (derivatives + macro
> brakes). A hard **`CMC_ONLY` firewall** makes any Binance/Bybit reach RAISE instead of silently
> serving exchange data (set at the momentum_cmc entry points). Validated CEX-free on **real CMC daily**
> history: DQ-safe at the live `top_k=5` band **[0.35, 0.80] → 23.5% worst-week DD** (« 30% DQ; the live
> 4h feed is safer still), `ta_rank` PASSES the CEX-free `make ab_regime --candle-source cmc_daily`. The
> locked `momentum_adaptive` (Binance-4h) stays registered + **byte-for-byte** as the dormant fallback;
> promotion to the LIVE default remains an operator env decision (`STRATEGY_NAME=momentum_cmc` +
> `live_tick.sh` sets `CMC_ONLY=true`). Full architecture + caveats: **`docs/cmc_candles.md`**.

---

## 1. The central finding: there is no long-only TA edge on this universe

Confirmed **five independent ways**, all at realistic DEX friction:

| Test | Result | Where |
|---|---|---|
| ICT POI/MSS/FVG entry | negative OOS expectancy | `docs/findings.md` §13–§15 |
| Trend pullback (selective, 4h) | only ETH holds; ~0.6 trades/wk (too rare) | `scripts/validate_trend.py` |
| Trend loosened / 1h | enough trades, **edge gone** (all TEST negative) | probe |
| Friction sweep 0.10%→0.70% | net basket expectancy **negative even at 0.10%** | probe |
| Portfolio search, 2,338 rolling 7-day windows | **every** strategy median weekly return ≤ 0 | `scripts/validate_allocator.py` |

A 7-day result on 8 liquid majors is **variance around ~breakeven**, gated by the 30%-DD
disqualifier. So the objective is not alpha (there is none) — it is **survival + participation +
craft**: an agent that is nearly impossible to disqualify, participates in upside when the week
trends, trades enough to clear the floor, and showcases the TWAK/BNB toolchain.

## 2. What ships: the momentum allocator

Each rebalance (≈daily), over the 8 tokens:

1. **Rank** by trailing **120-bar** return.
2. **Hold the top-2** by *relative* momentum — the **ACTIVE stance** (`abs_filter=False`): the agent
   always deploys into the strongest names so it *actually trades* every rebalance (a PnL contest,
   not a sit-in-cash demo). Drawdown is controlled by the regime cap (step 4) + the DD halt, not by
   going fully to cash. *(The old risk-first cash filter — cash whenever momentum is negative — is a
   runtime toggle `ALLOC_ABS_FILTER=true`; with all 8 tokens currently down it would trade nothing.)*
3. **Size** the held tokens by **inverse volatility** (30-bar).
4. **Deploy ADAPTIVELY** — the deployment cap is *not* a frozen number. It scales with a **live
   risk-on score** (basket breadth + index trend + volatility, plus live Fear&Greed) into the
   **participatory band [0.40, 0.85]**: ~0.85 deployed when the basket is broadly trending up,
   pulled to ~0.40/cash when it isn't. *This is what makes the agent react to the unfolding week
   instead of a backtest fit.* Code: [`strategy/regime_score.py`](../src/ictbot/strategy/regime_score.py).
5. **Rebalance** the book toward those weights via **TWAK spot swaps** (sells before buys).

No SL/TP brackets — an AMM swap has no native stop. Risk control is the adaptive cap + cash filter +
diversification + a hard **drawdown halt** (NAV vs high-water mark → flatten + stop).

Code: [`strategy/momentum_allocator.py`](../src/ictbot/strategy/momentum_allocator.py) (dynamic cap),
[`strategy/regime_score.py`](../src/ictbot/strategy/regime_score.py) (regime → cap).

## 3. Why ADAPTIVE, not a frozen cap — and how it's validated

A backtest only describes events that already happened; freezing a cap to one (net-bearish)
historical window is the hindsight trap. The contest week's regime is unknown, so the agent
**adapts deployment to live signals** and is **validated forward**, not tuned to the past.

**Deployment reacts to the live regime** (verified over the 8-token history — this is *behaviour*
we control, not a return prediction; entry-regime can't predict the next week, because there's no edge):

| regime (bar) | mean deploy cap |
|---|---|
| BULL | **0.78** (deploys, captures upside) |
| BEAR | **0.45** (defends → cash) |
| CHOP | **0.52** (volatility-cut) |

**Adaptive vs a static high cap** (rolling-7-day, 0.70% friction): the adaptive agent keeps most
of the upside of a static 0.85 cap (**p95 +10.4% vs +12.3%**) at materially **lower worst-week DD
(17.6% vs 22.3%)** — better risk-adjusted exposure, **DQ-safe (< 30%)**, **~11.5 trades/wk ≥ 7**.
*(These §3 figures are the original adaptive-vs-static comparison run; the ACTIVE-stance
re-validation in §7 — worst-week DD 17.3%, ~15.4 trades/wk — is the current vintage the README
headlines.)*

**The backtest is a cross-regime sanity check, not the basis for the config.** The PRIMARY forward
evidence is a **daily paper run from now → the contest** on unseen data (`make forward_report`).
The band is a runtime override (`ALLOC_CAP_FLOOR` / `ALLOC_CAP_CEILING`, `ALLOC_ADAPTIVE`) — dial it
down near 06-22 if the week looks volatile. *Anti-overfit:* the regime logic is simple, principled
linear maps — NOT grid-searched on the sample — so we don't re-introduce the bias we're avoiding.

## 4. Architecture — the THREE pillars (Track 1: "Powered by CMC + Trust Wallet + BNB AI Agent SDK")

The validated trading core is wrapped by an agent layer so all three required pillars have a genuine
role, plus a natural-language face ("natural-language strategy in, on-chain execution out"):

```
   config/strategy.md  (natural-language strategy — "the rules you set")
            │  parse once → AllocatorParams + deploy band [0.40,0.85]
            ▼
  ┌─ each rebalance tick ───────────────────────────────────────────────┐
  │  ① CMC   → price + Fear&Greed            (PILLAR 1: the eyes)         │
  │  ② regime score → adaptive cap → target weights                      │
  │  ③ rationale.explain(...) → plain-language decision  (the agent's voice)│
  │  ④ TWAK  → spot swaps to targets (native gas) (PILLAR 2: the hands)   │
  └──────────────────────────────────────────────────────────────────────┘
   ⑤ BNB AI Agent SDK → ERC-8004 on-chain identity (PILLAR 3: the identity)
       (two wallets, linked on-chain: the PINNED identity wallet 0xEb7b…9655 holds
        agentId 133085 + signs the per-tick heartbeat; the twak trading wallet
        0xE8A3…6215 trades + is contest-registered and is declared in the NFT metadata)
```

- **Pillar 1 — CMC (eyes):** [`data/cmc.py`](../src/ictbot/data/cmc.py) — live price + Fear&Greed
  (drives the regime score); 4h candles from public Binance (universal fallback). *(Funding rates are
  perp-specific; this is a spot agent, so its CMC signals are F&G + breadth + trend.)*
- **Pillar 2 — Trust Wallet / TWAK (hands):** [`exec/twak_client.py`](../src/ictbot/exec/twak_client.py)
  + [`bsc_spot_live.py`](../src/ictbot/exec/bsc_spot_live.py) — signs + executes spot swaps (native gas;
  gasless via MegaFuel when `TWAK_GASLESS` is enabled);
  `twak compete register`. Sole signer.
- **Pillar 3 — BNB AI Agent SDK (identity):** [`agent/identity.py`](../src/ictbot/agent/identity.py) —
  mints the ERC-8004 identity NFT via `bnbagent`. twak doesn't export its key, so bnbagent
  **self-manages the identity wallet from a password** — now **pinned** (`AGENT_PRIVATE_KEY`, see the
  2026-06-12 correction above) so the identity can never regenerate away again; the profile links the
  twak trading wallet (`AGENT_TRADING_ADDRESS`). Identity key ≠ funds key — better separation. The
  live mint (agentId **133085**) went **direct-gas** at ~$0.07 after patching the SDK's stale 3-gwei
  floor; MegaFuel gasless remains a flip (`AGENT_USE_PAYMASTER=true`) once the sponsor policy is set.
- **Agent voice:** [`agent/strategy_spec.py`](../src/ictbot/agent/strategy_spec.py) (NL → params) +
  [`agent/rationale.py`](../src/ictbot/agent/rationale.py) (per-tick plain-language decision, journaled).

- **Data plumbing:** [`data/cmc.py`](../src/ictbot/data/cmc.py) — CMC for live price + Fear&Greed (when
  keyed); 4h candles from public Binance (universal fallback).
- **Strategy:** the allocator (pure, vectorised + live paths, cross-checked by tests).
- **Backtest:** [`engine/portfolio_replay.py`](../src/ictbot/engine/portfolio_replay.py) —
  target-weight book, turnover friction, rolling-window stats.
- **Execution:** [`exec/twak_client.py`](../src/ictbot/exec/twak_client.py) (sim + live TWAK) →
  [`exec/bsc_spot_live.py`](../src/ictbot/exec/bsc_spot_live.py) (`TwakSpotBroker` rebalancer).
- **Runtime:** [`scripts/run_allocator.py`](../scripts/run_allocator.py) — one rebalance tick;
  drawdown halt; journal. Driven on a schedule (daily). Sim by default (no keys); live signs
  real swaps via the trust-wallet-cli under `ENABLE_LIVE_TRADING=true`.
- **Registration:** [`scripts/register_agent.py`](../scripts/register_agent.py) → the built-in
  **`twak compete register`** / `status` (no CompetitionRegistry ABI to wire); optional
  `twak erc8004 register` for the agent-identity NFT.
- **Live integrations (wired + verified):** CMC keyed Pro API is **live** (real price + Fear&Greed,
  drives the regime score); TWAK creds **validated** (`twak price`, `twak swap --quote-only` return
  real BSC quotes). Execution stays in **sim** until `twak setup` creates the agent wallet.
- **Untouched:** the ICT path + Binance/Delta CEX brokers stay whole as the separate product.

## 5. What was explicitly rejected (no path back)

- **Perps / leverage** — **OUT, by organizer ruling (BNB Chain, Gwen, 2026-06):** *only
  transactions through the **TWAK swap interface** count toward contest P&L.* Scoring inspects
  on-chain transactions and counts only swap-interface ones — that's how a **trade** is told
  apart from a **deposit** deterministically. Perps can't be tracked that way, so to keep
  scoring fair for everyone they are excluded — a perp leg would contribute **zero** to P&L
  regardless of how it's built. (Secondary, independent reason: TWAK signs spot swaps /
  transfers / DCA / limit-orders only and **cannot sign perps for agents** anyway; a perp leg
  would need a custom web3 + perp-DEX broker signing *outside* TWAK, which the scoring rule now
  makes pointless for the contest.) A perp leg (Aster + bnbagent signer) was prototyped and
  then removed once this ruling landed. **Out** — see `docs/strategy_playbook.md` Part B
  (retained as future-work research only).
- **The ICT entry stack** — negative OOS expectancy (`findings.md`). Kept only as a fallback.
- **Per-token trend signals** — no basket edge even at 0.10% friction (§1).

## 6. Run it

```bash
make validate_allocator                 # backtest: adaptive deployment-by-regime + DQ-safe gate
make run_allocator ARGS="--reset"       # start a fresh forward paper run
make run_allocator                      # one adaptive rebalance tick (journal -> data/journal/)
make forward_report                     # FORWARD track record on unseen data (run daily now -> 06-22)
make run_allocator ARGS="--mode live"   # real BSC swaps (needs [bsc] + ENABLE_LIVE_TRADING)
```

**Forward validation (the real test):** schedule a daily tick from now → the contest
(`0 0 * * * cd <repo> && python scripts/run_allocator.py`) and read `make forward_report`. The
backtest is a cross-regime sanity check; the forward paper run is the out-of-sample evidence.

**Honest bottom line:** we do not claim 30–40% — nobody can on these tokens in 7 days. We claim
the **best risk-controlled, DQ-proof, regime-adaptive long-only agent**, signed end-to-end by
TWAK — that **participates** when the live week is risk-on and **defends** when it isn't.

## 7. Operational hardening (live-safe, unattended)

The strategy is unchanged (worst-week DD 17.3%, ~15.4 trades/wk — re-confirmed); these are
*execution/runtime* safeguards so an unattended live cron can't lose the contest to a mechanical
failure. All are additive and covered by tests (`test_run_allocator_hardening`, `test_trade_floor`,
`test_api_reads`, extended `test_bsc_spot_live`/`test_twak_cli`).

- **State integrity:** `save_state` writes atomically (`tmp` + `os.replace`) so a crash mid-write
  can't corrupt the high-water mark and silently defeat the drawdown halt.
- **Execution resilience:** a failed `twak swap` returns `ok=False` (it no longer raises) so one
  bad swap can't crash a rebalance mid-flight; `_run` retries transient errors with backoff; a live
  execute is only "ok" with both an amount-out **and** a tx hash (no silent zero-fill). Failed swaps
  + a per-swap min-notional floor are journaled; `emergency_flatten` is best-effort-complete and logs
  any residual exposure CRITICAL.
- **Risk guards:** a tick **skips** (never trades / never false-halts) on an invalid/zero price, a
  zero NAV, or stale candles (live, > 12h). F&G unavailability degrades to breadth+trend and is
  journaled. `--resume` clears a drawdown halt cleanly.
- **Idempotency:** per-mode `flock` (Python + the cron wrappers) prevents two ticks double-executing
  the same rebalance on cron overlap / restart.
- **Trade floor (≥7):** cumulative swaps are tracked; if behind pace within
  `TRADE_FLOOR_LOOKAHEAD_DAYS` of `CONTEST_END`, the agent banks bounded round-trip **FLOOR_NUDGE**
  trades (~0 NAV impact) so a flat-regime week can't miss the minimum and DQ us.
- **Live preflight + reconciliation:** a live tick fails fast (clear message) without creds / wallet
  password / `ENABLE_LIVE_TRADING`; on-chain balances are reconciled against the journal and any
  drift is surfaced as a `RECON_DRIFT` event. Boot guard: `TWAK_MODE=live` requires TWAK creds.
- **Observability:** Mission Control surfaces halt reason, sim-vs-live journal mismatch,
  trades-toward-7, and per-rebalance failed-swap counts (`src/ictbot/api/reads.py` + the React UI).

## 8. PnL campaign (2026-06-13) — target +5–7%, lock the good path, cap the bad one

**Goal.** Between now and the 06-21 submission, push the **forward paper track** (the
judge-visible dashboard NAV) toward a credible **+5–7%**, with both a **10% drawdown halt** and
a **profit-lock ratchet**. Honest framing first: this strategy has **no fixed edge** (§1) — the
campaign maximizes the *probability* of a good week and **keeps** it if it happens; it cannot
manufacture a trend.

**What the sweep found** (`make sweep_campaign` → [campaign_sweep.json](../data/journal/campaign_sweep.json);
1,728 lever combos, rolling 9-day windows, 0.70% friction, the campaign rules replayed on each
window's equity):

- **+5% is regime-dependent.** Across full history ~**21%** of 9-day windows reach +5%; in the
  *recent* choppy regime only ~**9%** (H1 ~35% vs H2 ~9% on a 50/50 time split). We state this
  plainly rather than quote the rosy full-history number.
- **The profit-lock genuinely earns its place.** For the chosen config, raw end-of-window
  P(≥5%) = 16.7%, but **with the ratchet = 21.3%** — it converts ~4.6 pts of "spiked to +5%
  then gave it back" into kept gains.
- **A 25% halt buys nothing.** Widening 10%→25% leaves P(+5%) flat (~21%) and only deepens the
  worst case (realized −14.6% → −17%), because the winning windows never draw down that far —
  only the losers do. **So we keep 10%** (strictly better risk-adjusted; tested at the user's
  request).
- **Dropping DOGE** (the highest-vol memecoin) more than doubled the recent-regime shot
  (H2 ~9% vs ~4% full universe) — a first-principles tail-risk cut, robust in both halves.

The campaign band [0.40, 0.90] validates at **worst-week 15.6%** (`make validate_allocator` with
the campaign `.env`) — vs the committed-baseline 0.85 band's 17.3% (§7). Both are well inside the
30% DQ line; the wider ceiling adds risk-on upside without materially deepening the worst week
(the adaptive cap rarely reaches the ceiling in the bear windows where drawdowns occur).

**Config deltas (forward sim track only — the committed defaults stay the validated baseline):**

| Lever | Baseline | Campaign | Why |
|---|---|---|---|
| Universe | 8 tokens | **7 (drop DOGE)** | cut the highest-vol tail |
| `ALLOC_LOOKBACK` | 120 | **60** | faster momentum; best in *both* time halves |
| `ALLOC_REBAL_BARS` | 6 (daily) | **3 (12h)** | more reactive; the cron cadence IS the rebalance cadence |
| `ALLOC_CAP_CEILING` | 0.85 | **0.90** | deploy a touch more when risk-on |
| `MAX_DRAWDOWN_FRAC` | 0.05 (lib default) | **0.10** | the campaign rail (« 30% DQ); live week tunable |
| `PROFIT_LOCK_ENABLED` | false | **true** | arm +5%, trail 3%, bank +10% |

Set via the operator's `.env` (gitignored) + `data/journal/active_tokens.json` (the DOGE drop,
same file-config pattern as the kill switch). A clean clone runs the **validated baseline** —
the campaign is an operator overlay, not a code default.

**The ratchet (`scripts/run_allocator.py`).** A pure `_profit_lock_eval(state, nav, …)` decides
`{none, arm, bank, trail}` against the campaign anchor; the tick (and the intraday `--dd-watch`)
flatten + set a **separate** `profit_locked` flag on bank/trail. `profit_locked` is distinct from
the drawdown `halted` so `--resume` never re-opens a banked campaign — `--unlock-profit` does that
deliberately. Anchor set once with `--anchor-nav 1000` (so cum-return = the dashboard's number).
Journals: `CAMPAIGN_ANCHOR`, `PROFIT_LOCK_ARMED`, `PROFIT_LOCK` (+ a `profit_lock` sub-dict on
every REBALANCE row). 35 new tests ([test_profit_lock.py](../tests/test_profit_lock.py),
[test_daily_floor.py](../tests/test_daily_floor.py)).

**≥1-trade/day floor.** The brief asks ≥1/day *and* ≥7/week; the old tracker only guaranteed the
weekly total. `--ensure-daily-floor` (cron'd near end-of-day UTC in the contest window, `TRADE_FLOOR_DAILY=1`)
banks one ~0-NAV round-trip if a day would close with zero swaps.

**Dead-cron postmortem (the reason this section exists).** The forward "daily paper run" claimed
in §6 was **not actually running**: the crontab + `scripts/dd_watch.sh` pointed at a stale
`"BNB Hack * CMC"` directory that no longer exists, so every fire exited silently — the Jun 8–12
ticks were all manual. Fixed: both wrappers repoint to `BNB-Hack-CMC`, all campaign levers moved
into `.env` (the cron, the dashboard sim-tick, and the watcher were trading *different* configs
into the same journal), and the crontab now runs a 12h forward tick + a 15-min risk watcher.

**Runbook.**

```bash
make sweep_campaign                              # re-rank the lever grid (writes campaign_sweep.json)
python scripts/run_allocator.py --mode sim --anchor-nav 1000   # (re-)set the campaign anchor
make run_allocator                              # one campaign tick (reads the .env overlay)
python scripts/run_allocator.py --mode sim --dd-watch          # intraday halt + ratchet check
python scripts/run_allocator.py --mode sim --resume            # clear a DD halt (keeps a profit lock)
python scripts/run_allocator.py --mode sim --unlock-profit     # deliberately re-open a banked campaign
```

Cron (local IST = UTC+5:30; installed 06-13): `40 5,17 * * *` forward tick · `*/15 * * * *`
sim watcher · `40 3 23-29 6 *` live-week daily floor (inert until `TRADE_FLOOR_DAILY=1`). Logs:
`data/logs/allocator_cron.log`, `data/logs/dd_watch_sim.log`.

**Live-week arming (Jun 20–21, operator).** Decide the live `--dd-cap` (10% is the validated
campaign rail; widen only with eyes open — a wider cap does not raise the upside), set
`ENABLE_LIVE_TRADING=true` + `TWAK_MODE=live` + `TRADE_FLOOR_DAILY=1`, and add the live
`live_tick.sh` + `dd_watch.sh live` cron lines. The contest week IS the held-out window.

## 9. Capability-arm config change: `breakout` re-registered (2026-06-13)

The stability sweep (`make sweep_arms`) found `breakout`'s original registered default
(entry 20 / exit 10 / daily rebal) was **UNSTABLE** — worst-week DD swung to 31.7% with a 24-pt
spread, failing a data-window segment (it deploys heavy after a breakout, then a reversal craters the
book). A shorter exit channel + faster rebalance fixes it: **entry 20 / exit 5 / 12h rebal** grades
**ROBUST** (ddMax 13.9%, spread 4.8%, holdout calmer than train → not a curve-fit). The 5-bar exit
flattens a loser before the reversal, exactly as the arm's design intended.

**Decision:** re-register `breakout`'s default to `entry_lb=20, exit_lb=5, rebal_bars=3`
([adapters/breakout.py](../src/ictbot/strategy/adapters/breakout.py)). This is a **stability fix on a
SIM-track capability arm, not an edge claim** (no long-only edge exists on this universe; §1). It does
**not** touch the locked `momentum_adaptive` contest default. `BNB_STRATEGY_05` inherits the new config.
Promotion of `breakout` to LIVE would still require a forward check + operator sign-off (Part 7 policy).

## 10. `grid` built last — the net-inventory grid (2026-06-14)

`grid` was the last unbuilt playbook arm (#5, BELOW-AVG: "worst risk profile for a DD-gated contest" —
needs a net-inventory model + a hard range stop). Built as
[adapters/grid.py](../src/ictbot/strategy/adapters/grid.py): hold MORE of a token the lower it sits in
its Donchian range (buy dips), less the higher (sell rips), and **flatten on a breakdown below the
range** (the hard stop). A price-responsive target weight — no resting two-sided orders (TWAK signs
spot only). Registered as `grid` / `BNB_STRATEGY_09`.

**Measured grade (`make stability` / `make campaign`): FRAGILE.** Survival ✅ but worst-week DD **21.6%**
(the second-riskiest arm, just inside the 25% rail) with **53.7 trades/wk** (the grid whipsaw) — exactly
the playbook's call. The **hard range stop is what keeps it DQ-safe**: it caps the breakdown tail that
would otherwise make a naive grid UNSTABLE. The sweep finds no ROBUST config (every grid config is
FRAGILE; lower-spread variants carry a curve-fit smell + 100+ t/wk). So grid completes the playbook as a
**capability/diversification arm, not a contest candidate** — high turnover, fragile, DQ-safe-but-thin.
It stays SIM-track; like every challenger it would need a forward check + operator sign-off for LIVE.

## 11. Playbook wired + PnL/win-rate scoreboard (2026-06-14)

The research playbook is now wired to the implementation: every Top-10 family maps to a registered +
validated arm in [strategy_playbook.md](strategy_playbook.md) §11 (auto-generated by `make playbook`,
parity-pinned by `tests/test_playbook_parity.py`), and PnL + win-rate are surfaced as a **scoreboard over
the survivors** — never an edge claim. Risk-first survival stays the hard GATE. Honest measured result:
at 0.70% friction every long-only arm is net-negative over the full backtest, which is the §1 "no edge"
thesis made visible. Step-by-step procedure: [strategy_validation_runbook.md](strategy_validation_runbook.md)
(`make validate_all`). No new strategies; the locked `momentum_adaptive` is untouched.

## 12. `breakout` resolved → research-only, NOT a contest candidate (2026-06-14)

§9 re-registered `breakout` to `entry20/exit5/rb3` after it graded ROBUST (13.9% DD) on that window. On
a **later 2500-bar window it grades UNSTABLE** (worst-week DD **31.2%**, spread **23.6%**, fails a segment
+ flips on friction) — and a fresh `make sweep_arms --arm breakout` finds **no ROBUST config**: the best
alternative (`entry30/exit15/rb6`) is only FRAGILE and carries a **large overfitΔ (−14.3% → curve-fit
smell)**. This is the honest signature of a **data-window-sensitive** arm, exactly what the stability
harness exists to expose.

**Decision: flag `breakout` as a research/capability arm, like `grid` — NOT a contest candidate.** We do
**not** re-register it to the FRAGILE/overfit config (that would be curve-fitting to one window). The
default stays `entry20/exit5/rb3` for reproducibility; `BNB_STRATEGY_05` remains a clearly-labelled
**research** alias. No edge claim. The decision loop already protects against it: `recommend_arm`
(`scripts/contest_readiness.py`) only ever surfaces a **ROBUST** challenger, so an UNSTABLE/FRAGILE
`breakout` can never be recommended, and a single noisy survival PASS can never promote it. The contest
arm stays the incumbent `momentum_adaptive` unless a genuinely robust, forward-validated challenger
clears every gate.
