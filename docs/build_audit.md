# Build-Completeness Audit — BNB Hack × CMC Trading Agent (FINAL, full-stack)

> **Round-5 update (2026-06-11) — REMEDIATED.** Every finding was independently re-verified
> against the live tree (3 adversarial passes). **23 of 24 confirmed real; only CMC-4 was
> refuted to "inert hygiene"** (the dead `lo` param caused no behavior bug at the default
> symmetric band). All confirmed findings were then **fixed in four tested waves**, each gated
> green before the next:
> - **Wave 1 — exec safety + reaction time:** A1 explicit `--slippage` (`TWAK_SLIPPAGE_PCT/FLAG`);
>   A2 `emergency_flatten` retry/backoff; A3 guarded `broker.prices()` → rc 2; **G1/G2 the new
>   `--dd-watch` flatten-only fast monitor** (`run_allocator.py --dd-watch` + `scripts/dd_watch.sh`,
>   shares the per-mode lock, never opens/flips).
> - **Wave 2 — pillar-3:** B1 persist the minted `agentId` via a public `rewrite_env_key`; B2 log
>   heartbeat failures; H3/H4 gate the ERC-8004 "minted" badge + flag-aware footer on `agent_id>0`.
> - **Wave 3 — hardening:** A4 absolute kill-switch paths; E1 scope the CEX boot guard off the
>   TWAK-live path; C1 x402 `attempted/failed` journal flags; C2 enabled-but-unavailable warning;
>   D1 `tick("live")==2` integration test; D2 `FLOOR_NUDGE_FAILED` event + failure test.
> - **Wave 4 — FE honesty + CMC hygiene:** H1/H2/H5/CMC-1 thread the `live` freshness flag (no
>   green over a frozen snapshot); CMC-3 probe/fetcher alignment; CMC-4 `lo`-clamp + docstring;
>   CMC-5 `last_credit_count` added to the schema/types/card; CMC-2 ledger docstring corrected.
>
> **Verification:** full suite **1081 passed** (20+ new tests; the only 2 failures are pre-existing
> `test_news_alert` CPI-dedup, unrelated); ruff clean on all touched files; `tsc --noEmit` exit 0;
> production bundle builds from a clean `/tmp` path. **All safety invariants preserved** — SIM path
> byte-identical, no fund key touched/deployed, the DD-monitor cron documented (not enabled) and
> contest-window-only. The findings below are retained as the audit trail; status is now FIXED.

> **Scope:** "Recheck the build — no half-finished code or under-implementation," while the agent
> is **live-capable on BSC mainnet with real funds**, near submission. **This final pass is
> full-stack** — backend exec/runtime + the dashboard FE↔BE + on-chain pillars + a reaction-time
> architecture review — and every finding was **independently re-verified against the code and
> adversarially refuted** (false positives rejected, listed in §4).
> **This document records findings only.** No code was changed and no mainnet action was taken.
>
> **Round-3 update (2026-06-10):** re-audited the post-FE/BE-pillars tree with a multi-agent
> full-stack pass (15 sub-agents) + a manual re-read. The backend findings (Themes A–F) were
> re-confirmed against the current line numbers; **two new themes** were added — **Theme G
> (reaction-time / two-speed loop)**, prompted by a team domain insight ("drawdown = reaction
> time"), and **Theme H (dashboard honesty / misleading-green-on-fallback)** from the FE audit.
> A **worth-it-vs-hype verdict** is attached to every finding, and **3 candidate findings were
> rejected** (§4). Implementation is pending the team's go-ahead — **nothing is fixed yet**.

_Generated 2026-06-10. Reflects the tree at that time (post the FE/BE pillars + onchain.py update)._

> **Round-4 update (2026-06-10) — CMC Startup-tier "super agent" (commit `b185501`):** step-by-step
> re-audit of the free→Startup tier upgrade (14 sub-agents, adversarially verified). **Headline: it
> shipped CLEAN — all three safety invariants hold and there is NO half-finished implementation.**
> **10** minor findings confirmed (mostly LOW), **0** false-positives, **35** areas verified-solid.
> Full detail in **§0** directly below.

---

## 0. Round 4 — CMC Startup-tier upgrade (the most recent core change)

The free→**Startup** tier upgrade (`cmc_client.py` rate-limiter + credit-budget ledger, `cmc_intel.py`
market intel, an enhanced regime in `regime_score.py`, a `universe_overlay` tilt, and the market-intel
dashboard cards) was audited against three invariants. **All three hold:**

- ✅ **(1) Flags-OFF ⇒ contest decision BYTE-IDENTICAL.** The 3 new flags (`CMC_INTEL_ENABLED`,
  `CMC_REGIME_ENHANCED`, `ALLOC_UNIVERSE_TILT`) all default **OFF**
  ([settings.py:232-236](src/ictbot/settings.py#L232)); the enhanced regime score reduces **bit-for-bit**
  to the old score when `intel=None` (numerically verified over 200 matrices — **max abs diff 0.0**);
  the universe tilt **preserves total deployment** (rescales to `sum_before`); building intel never
  mutates baseline state. No A/B leakage into the live trade decision.
- ✅ **(2) Budget / rate-limit DEGRADES, never blocks.** `CMC.get()` never raises into the tick; the
  rate-limit wait is bounded (`cmc_max_wait_s`) → stall → cache/None; **pre-request** budget enforcement
  blocks an over-budget call *before* spending; ledger writes are atomic (`tmp`+`os.replace`); the boot
  guard refuses budgets above the hard caps (10k/day, 300k/mo, 30rpm).
- ✅ **(3) NO half-finished work.** No `TODO`/`FIXME`/stub/`NotImplementedError` in the new code (one
  benign "seam for tests" comment); the `error_code "0"` /v3 gotcha is normalized at the **single** seam;
  the capability short-circuit fails safe; every **consumed** endpoint is fetched + parsed; the
  market-intel FE↔BE contract is 1:1; empty/disabled states degrade gracefully; **CMC attribution present**.

The confirmed findings are **minor polish** — none touch the trading decision or risk safety:

| # | Sev | Worth-it | Area | Location | Gap |
|---|-----|----------|------|----------|-----|
| **CMC-1** | MED | REAL (cosmetic) | FE misleading-green | [MarketIntelPanel.tsx:110,137-145](web/src/components/MarketIntelPanel.tsx#L110) | the "live" badge ignores the freshness flag → green "live" over the frozen static snapshot (same class as **H1/H2**) |
| **CMC-2** | MED→LOW | REAL (bounded) | Credit-ledger concurrency | [cmc_client.py:116,151-160](src/ictbot/data/cmc_client.py#L151) | ledger is thread-safe + atomic but **not cross-process** (no flock) → concurrent allocator+dashboard writes **under-count** credits (degrades softer, never blocks; CMC enforces the real hard cap server-side — WONTFIX defensible) |
| **CMC-3** | LOW | REAL (hygiene) | Probe/fetcher drift | [probe_cmc.py:33,37,39](scripts/probe_cmc.py#L33) ↔ [cmc_intel.py:88](src/ictbot/data/cmc_intel.py#L88) | `global-metrics/quotes/historical` is fetched but **not probed** (capability can't short-circuit it if tier-gated); `listings/latest`+`trending/latest` are probed but **never consumed**; the commit msg overstates "listings/trending" coverage |
| **CMC-4** | LOW | REAL (latent) | `universe_overlay` contract | [universe_overlay.py:23-44](src/ictbot/strategy/universe_overlay.py#L23) | `momentum_tilt(lo=…)` param is **dead** — the real band is `[2-hi, hi]`; the docstring "bounded to [lo,hi]" is only true for symmetric bands (inert at defaults) |
| **CMC-5** | LOW | REAL (hygiene) | FE↔BE contract drift | [cmc_client.py:368](src/ictbot/data/cmc_client.py#L368) ↔ [schemas.py:290-302](src/ictbot/api/schemas.py#L290) | `telemetry()` emits `last_credit_count`, absent from `CmcApiOut`/`types.ts` (pydantic strips it; `snapshot.json` carries it raw → proves the static fallback isn't schema-validated) |
| **A3↺** | LOW | REAL (prior, unchanged) | Exec safety | [run_allocator.py:345](scripts/run_allocator.py#L345) (was :303) | prior **A3** still holds — `broker.prices()` unguarded → a CMC+Binance double-miss `RuntimeError` aborts the tick before the bad-price/DD guards. The upgrade made the CMC leg degrade-not-raise, but the terminal `cmc.py:147` raise survives, so the abort path is intact |

**Prior findings reconciled:** **A1, A2, B2, C1, G1 are UNCHANGED** by the upgrade — the new intel/tilt
blocks are `try/except`-wrapped and flag-gated, so they add **no new raise-before-DD-halt path**, and the
DD-halt block is untouched. Only **A3**'s line shifted (303→345).

### Round-4 fix specs (all minor; none blocking)

| # | Lane | Files | Change |
|---|------|-------|--------|
| CMC-1 | FE | `MarketIntelPanel.tsx`, `MissionControl.tsx` | thread the `live` flag in; render green "live" only when `intel.enabled && live`, else amber "snapshot"/"off" (mirror StatusBar / the H1/H2 fix) |
| CMC-2 | BE | `cmc_client.py` | (optional) `fcntl.flock` + re-read-from-disk around the ledger read-modify-write (mirror `run_allocator._acquire_lock`); **or** accept WONTFIX (single-cron + ~12× headroom + server-side hard cap) and correct the docstring to "atomic, thread-safe" |
| CMC-3 | BE | `probe_cmc.py`, `cmc_client.py` | add `global-metrics/quotes/historical` to `PROBES`; drop the unconsumed `listings/latest`+`trending/latest` probes + the orphan `"listings":600` TTL; align the commit/docs wording to the consumed endpoints |
| CMC-4 | BE | `universe_overlay.py` | clamp `mult = max(lo, min(hi, …))` to honor asymmetric bands **or** drop the `lo` param + reword the docstring; add an asymmetric-band test |
| CMC-5 | BE | `cmc_client.py` (or `schemas.py`+`types.ts`) | drop `last_credit_count` from `telemetry()` (simplest) **or** add it to `CmcApiOut`+`CmcApi` and surface it in `CmcApiCard`; regenerate `snapshot.json` from the schema-validated endpoint |
| A3↺ | BE | `run_allocator.py` | (already specced in §6) wrap `prices = broker.prices()` in `try/except RuntimeError` → `return 2` |

**Bottom line:** the Startup-tier upgrade is **well-built and safe** — the contest trade path is provably
unchanged (flags off by default, regime reduces bit-for-bit), the new machinery **degrades, never
blocks**, and there is **no half-finished implementation**. The 6 items above are cosmetic / hygiene /
one bounded credit-accounting nicety / one re-confirmed prior exec-safety item — **none** affect the
trading decision or the risk safeguards.

---

## 1. Verdict

**The build is mature and production-grade — and the FE/BE work is high quality.** No broken
scaffolding, no `TODO`/`FIXME`/`NotImplementedError`, no fake/sample data in live render paths.
The FE↔BE contract is **1:1 with zero drift** (`tsc --noEmit` exits 0), the on-chain wallet read is
**real** (Multicall3 over public BSC RPC, CMC-first + Chainlink fallback, never raises), the
NodeReal/MegaFuel paymaster check is a **real RPC call**, and x402 "settled" receipts are **real
accounting** (0/0 honestly shown because x402 is off + the wallet is unfunded).

The remaining gaps are a **focused set**: (1) **live-execution safety** (slippage flag, flatten
retry, an unguarded price call); (2) **pillar-3 is code-ready but silently dead** (the minted
agent-id is discarded, so the on-chain heartbeat never fires) — and the dashboard's identity copy
**overstates** that as already-minted/active; (3) **dashboard honesty** — a couple of cards pulse
green "live" even when serving the frozen static snapshot; (4) **reaction time** — the agent is
single-speed, so the only intraday drawdown safeguard runs once per ~daily rebalance; (5) some
observability/test polish. None are unfinished *features*.

### What's verified solid (don't re-touch)

| Area | Evidence |
|---|---|
| **Rebalance runtime** | file-locked idempotency, atomic state writes (`tmp`+`os.replace`), live preflight, stale-candle + invalid-price + NAV guards, drawdown halt → emergency flatten, on-chain reconciliation drift, trade-floor auto-ensure — [scripts/run_allocator.py](scripts/run_allocator.py) |
| **CMC x402** | real `402 → EIP-3009 sign → resend` loop; amount/asset/network parsed from the live challenge (not hardcoded); per-call + session USDC caps; recipient-match guard; off by default; "settled" only on a real `200` — [src/ictbot/data/x402_cmc.py](src/ictbot/data/x402_cmc.py) |
| **TWAK execution** | real CLI shelling; fail-fast on permanent vs retry on transient; a live swap is `ok` only with **both** amount-out and tx hash — [src/ictbot/exec/twak_client.py](src/ictbot/exec/twak_client.py) |
| **On-chain "Real Funds" read** | `onchain.py` is a genuine **Multicall3.aggregate3** (native BNB + ERC-20 balanceOf over public BSC RPC), CMC-first pricing with Chainlink fallback, 45s TTL cache, `allowFailure=true`, **never raises** (degrades to cached/empty) — [src/ictbot/api/onchain.py](src/ictbot/api/onchain.py) |
| **NodeReal/MegaFuel paymaster** | `verify_paymaster_link()` is a **real** `eth_chainId` + `eth_getTransactionCount` + `pm_isSponsorable` round-trip to the keyed endpoint — not stubbed — [src/ictbot/agent/identity.py:113-164](src/ictbot/agent/identity.py#L113) |
| **Dashboard FE↔BE** | `web/src/api/types.ts` mirrors `schemas.py` **1:1, zero drift** (27 models field-for-field, nullability matched); `snapshot()` wraps every card in `_safe()` (one failure degrades that card, never 500s); static `snapshot.json` fallback carries **all** new fields (`pillars`/`served_at`/`wallet`/`x402_dex`); loading/error/stale states render explicit placeholders (no crash/NaN/blank) |
| **Dashboard honesty (mostly)** | `StatusBar` correctly flips to an amber **"demo snapshot"** badge on the static fallback; `PillarsPanel` body honestly shows **"not minted"** / **"policy off"** / **"key-free"**; the misleading-green is confined to two header pills + identity copy (Theme H) |
| **Settings/config** | every BNB key defined + documented; safe defaults (`enable_live_trading=False`, `twak_mode=sim`, `x402_enabled=False`, `twak_gasless=False`, `agent_heartbeat_enabled=False`); boot guards fire on misconfig |
| **SIM vs LIVE separation** | separate journals + state files; sim ledger never contaminates the live track |

---

## 2. Confirmed findings

8 backend (Themes A–F) + 6 dashboard/runtime (Theme H) + 2 reaction-time (Theme G), all
re-verified. **Status: all OPEN — verified, fix specs in §6, not yet implemented.** "Worth-it"
is my independent recheck of whether the change is genuinely warranted (vs audit-padding).

| # | Sev | Worth-it | Area | Location | Gap (one line) |
|---|-----|----------|------|----------|----------------|
| **A1** | HIGH | **REAL (slippage) · HYPE (max-usd)** | Exec safety | [twak_client.py](src/ictbot/exec/twak_client.py) `swap()` | live swaps never pass `--slippage` explicitly (rely on CLI default 1%); the audited `--max-usd` flag **does not exist** |
| **A2** | HIGH | REAL | Exec safety | [bsc_spot_live.py:146](src/ictbot/exec/bsc_spot_live.py#L146) | `emergency_flatten()` doesn't retry a failed leg during a drawdown |
| **A3** | MED | REAL | Exec safety | [run_allocator.py:303](scripts/run_allocator.py#L303) | `broker.prices()` is unguarded → a `RuntimeError` aborts the tick **before** the bad-price guard runs |
| **A4** | LOW | REAL (hardening) | Runtime | [kill_switch.py:24-26](src/ictbot/runtime/kill_switch.py#L24) | kill-switch uses **relative** `data/`+`.env` paths (not CWD-safe) — works under all *documented* launches; latent only |
| **B1** | HIGH | REAL | Pillar 3 | [register_agent.py:137](scripts/register_agent.py#L137) | minted `agentId` discarded → `agent_id` stays 0 → heartbeat dead |
| **B2** | MED | REAL | Pillar 3 | [run_allocator.py:397](scripts/run_allocator.py#L397) | heartbeat errors swallowed with a bare `except: pass` (no log) |
| **C1** | MED | REAL | Observability | [run_allocator.py](scripts/run_allocator.py) journal | journal can't tell x402 **disabled** vs **failed** vs **no-data** |
| **C2** | LOW | REAL | Observability | [x402_cmc.py](src/ictbot/data/x402_cmc.py) `fetch_x402` | x402 silently off when wallet pw missing but `X402_ENABLED=true` |
| **D1** | MED | REAL | Tests | [test_run_allocator_hardening.py](tests/test_run_allocator_hardening.py) | no test that a full `tick("live")` returns 2 on missing wallet pw |
| **D2** | LOW | REAL | Tests | [test_trade_floor.py](tests/test_trade_floor.py) | nudge untested under live failure; no `FLOOR_NUDGE_FAILED` journal event |
| **E1** | MED | REAL | Boot guard | [settings.py:643](src/ictbot/settings.py#L643) | live boot needs a **CEX key** even for the TWAK-only contest path |
| **F1** | verify | **OPERATOR (no code)** | x402 (docs) | [x402_cmc.py:228](src/ictbot/data/x402_cmc.py#L228) | resend header `X-PAYMENT` (x402-v2) vs docs' `PAYMENT-SIGNATURE`; settlement unproven (0 receipts) |
| **F2** | enhance | **WORTH (additive)** | x402 (docs) | [x402_cmc.py:43](src/ictbot/data/x402_cmc.py#L43) | `quotes/latest` is x402-payable too (probe-confirmed) — richer pillar-1 |
| **G1** | MED | REAL | Reaction-time | [run_allocator.py:316-329](scripts/run_allocator.py#L316) | **single-speed**: the only intraday-DD safeguard lives in the ~daily rebalance tick → a sharp DD is unreacted-to for ~24h |
| **G2** | MED | **WORTH (enhance)** | Reaction-time | design | a decoupled **fast DD-monitor** is worth building — reuses existing code, **zero overtrading risk** |
| **H1** | HIGH* | REAL (cosmetic) | FE honesty | [LiveWalletCard.tsx:27-34](web/src/components/LiveWalletCard.tsx#L27) | "live" badge is a **hardcoded** green pulse → claims live even on the static fallback |
| **H2** | HIGH | REAL | FE honesty | [PillarsPanel.tsx:119-122](web/src/components/PillarsPanel.tsx#L119) | green "chain 56" link pill rendered from the **frozen snapshot**, no freshness gating |
| **H3** | MED | REAL | Pillar-3 FE | [IdentityCard.tsx](web/src/components/IdentityCard.tsx) + [MissionControl.tsx:82-85](web/src/components/MissionControl.tsx#L82) | ERC-8004 badge + "mints + heartbeats … gaslessly" copy shown while `agent_id=0` (nothing minted) |
| **H4** | LOW | partial | FE honesty | [MissionControl.tsx:82-85](web/src/components/MissionControl.tsx#L82) | footer strapline asserts "CMC (x402) reads · gasless heartbeat" regardless of the live flags |
| **H5** | LOW | partial | FE stale | [StatusBar.tsx:59](web/src/components/StatusBar.tsx#L59) | "last tx Nd ago" grows off the frozen snapshot ts (demo badge already discloses it) |

\* H1 is filed HIGH within the "misleading-green / real-funds framing" lens, but it is **cosmetic**
(no functional/financial effect, and `StatusBar` already discloses the truth one row over). Treat
as a quick judge-honesty polish, not a safety fix.

---

### Theme A — Live-execution safety (mainnet money risk)

#### A1 · HIGH · Live swaps don't pass `--slippage` explicitly  — *and the audited `--max-usd` flag doesn't exist*
**Where:** `CliTwakClient.swap()` in [twak_client.py](src/ictbot/exec/twak_client.py).
The CLI args are built with amount, from/to, `--chain`, `--json` (+ optional `--password`, gasless
flag) and **never append a `--slippage` flag** — slippage is left to the CLI default.

**Worth-it recheck (ran `twak swap --help` on the node-26 PATH):**
- ✅ **`--slippage <pct>` is REAL** (default `"1"`). Passing it explicitly (and making it tunable)
  is worth doing — it lets us *tighten* below 1% if we want less sandwich exposure, and removes the
  silent reliance on a CLI default that could change.
- ❌ **`--max-usd` is HYPE** — there is **no such flag**. The CLI exposes `--usd <amount>` (an
  *input-amount* mode: "swap a USD-equivalent amount of the source token"), which is a **different
  thing** and would *change* swap semantics if mis-wired. Position size is **already capped
  upstream** by the allocator (deploy cap + `min_swap_usd` + the target weights), so no per-swap
  USD cap flag is needed or available. **Drop the max-usd part of this finding.**

**Fix (A1, trimmed):** add `TWAK_SLIPPAGE_PCT` (default `1.0`) + `TWAK_SLIPPAGE_FLAG`
(default `--slippage`); on `execute=True` append `[flag, str(pct)]` **only when the flag string is
non-empty** (trivially disableable, mirrors `twak_gasless_flag`). No `--max-usd`.

#### A2 · HIGH · `emergency_flatten()` doesn't retry a partial failure
**Where:** [bsc_spot_live.py:146-165](src/ictbot/exec/bsc_spot_live.py#L146).
On a drawdown halt it sells every token → USDT; each leg is independent and a failed leg is logged
`CRITICAL` with residual exposure — but there is **no retry/backoff**. A partial flatten leaves the
agent holding the very tokens it was trying to dump during a drawdown. **Confirmed** (`emergency_flatten`
has exactly one caller, the DD-halt block).
**Fix:** after the first pass, collect failed legs and re-`swap` each ~3× with backoff before the
`CRITICAL` log; stay best-effort-complete (never raise).

#### A3 · MEDIUM · `broker.prices()` can crash the tick before the price guard runs
**Where:** [cmc.py:117-129](src/ictbot/data/cmc.py#L117) `price()` raises `RuntimeError` when CMC +
Binance both fail; [run_allocator.py:303](scripts/run_allocator.py#L303) calls `broker.prices()`
**unguarded**, so the invalid-price guard immediately below (:306-309) **never gets to run** — the
exception propagates and aborts the tick with a traceback. **Confirmed.**
**Fix:** wrap `prices = broker.prices()` in `try/except RuntimeError` → print a reason + `return 2`,
matching the adjacent `bad_px`/NAV guards.

#### A4 · LOW · kill-switch uses relative `data/`+`.env` paths (latent CWD-fragility)
**Where:** [kill_switch.py:24-26](src/ictbot/runtime/kill_switch.py#L24) —
`KILL_SENTINEL = Path("data")/…` and `ENV_FILE = Path(".env")` are **relative**, unlike the
absolute `JOURNAL_DIR`/`LOGS_DIR` in `settings.py`. **Re-verified, but the dramatized impact is
overstated** (verifier downgraded MED→LOW): every *documented* launch is safe — Render/Docker
`WORKDIR /app` + the `ictbot-api` console script `os.chdir(PROJECT_ROOT)`, and `make api` runs from
the repo root. The bug only manifests if someone runs `uvicorn ictbot.api.app:app` from a foreign
CWD. Still worth a 2-line hardening so the existing `os.chdir` stops being the *only* thing keeping
the safety switch correct.
**Fix:** `from ictbot.settings import DATA_DIR, PROJECT_ROOT`; `KILL_SENTINEL = DATA_DIR/…`,
`ENV_FILE = PROJECT_ROOT/".env"`. No behavior change under any current launch.

---

### Theme B — Pillar 3 (BNB AI Agent SDK identity + heartbeat) silently dead

Root cause is one line; it has a backend half (B1/B2) and a dashboard half (H3).

#### B1 · HIGH · `register_agent.py` discards the minted `agent_id`
[register_agent.py:137](scripts/register_agent.py#L137): `res = identity.register_identity()` then
`print(f"  -> {res}")` — the freshly-minted ERC-8004 token id is **printed, never persisted**. So
`settings.agent_id` stays `0` and `write_heartbeat()` no-ops forever (it guards `if not aid … return`),
so **pillar-3's recurring on-chain heartbeat never fires**, with no error. The SDK return is
`{success, transactionHash, agentId:int, receipt, agentURI}` (confirmed in the `.venv` source).
**Fix:** expose a public `rewrite_env_key` wrapper of the atomic `_rewrite_env_key`
([kill_switch.py:56](src/ictbot/runtime/kill_switch.py#L56)); after the mint do
`aid = res.get("agentId"); if aid: rewrite_env_key("AGENT_ID", str(aid))` + print it prominently.

#### B2 · MEDIUM · heartbeat block swallows all errors with no log
[run_allocator.py:397-402](scripts/run_allocator.py#L397): `except Exception: pass` — a broken
heartbeat is invisible in LIVE, where on-chain activity is the whole point of the pillar.
**Fix:** `except Exception as e: print(... heartbeat failed ...)` (keep best-effort, don't re-raise);
optional `heartbeat_ok` marker on the LIVE journal row.

---

### Theme C — Observability of best-effort degradations

#### C1 · MEDIUM · x402 failures invisible per-tick
The x402 block sets `x402_dex = None` on disabled / failed / no-data alike; the journal can't
distinguish them. A silently-exhausted Base-USDC wallet would stop settlements with no per-tick trail.
**Fix:** add `x402_attempted` / `x402_failed` to the REBALANCE journal entry; log at INFO when x402
fails after a prior success. (Backend-only; the schema ignores extra keys.)

#### C2 · LOW · x402 silently disabled when wallet pw missing but `X402_ENABLED=true`
`available()` returns False (→ `fetch_x402` returns None) with **no warning**, so an operator can
believe pillar-1 is active when it isn't. **Fix:** one `log.warning` in `fetch_x402()` when
`x402_enabled` is true but `available()` is false, naming the missing piece.

---

### Theme D — Test coverage gaps

#### D1 · MEDIUM · no integration test that a live tick blocks on a missing wallet password
`_live_preflight()` is unit-tested, but nothing asserts the full `tick("live", …)` returns `2` when
the wallet pw is absent — so a refactor that bypassed the preflight would build a broker, fail every
swap, and still `return 0`, masking the failure from rc-based monitoring.
**Fix:** add `test_live_tick_preflight_missing_wallet_pw` asserting `tick("live", 0.3) == 2`.

#### D2 · LOW · trade-floor nudge untested under live failure; failed nudge not journaled
`_ensure_trade_floor()` is tested only against `SimTwakClient` (success path); when it can't bank
the floor (`banked == 0`) the only trail is a stdout WARNING — no journal event.
**Fix:** a mocked-`CliTwakClient`-failure test; add a `FLOOR_NUDGE_FAILED` journal event when
`banked == 0`.

---

### Theme E — Boot guard scope

#### E1 · MEDIUM · live-trading boot guard requires a CEX key even for the TWAK-only path
[settings.py:643-658](src/ictbot/settings.py#L643): `ENABLE_LIVE_TRADING=true` unconditionally
triggers a guard keyed on `settings.exchange` (default `delta`) that **refuses to boot without
`DELTA_API_KEY`/`SECRET`** — even though the contest agent trades via TWAK and uses no CEX. It works
today only because legacy CEX keys sit in `.env`; cleaning `.env` for submission would break live boot.
The separate TWAK guard (`twak_mode=="live"` requires TWAK creds, [settings.py:685](src/ictbot/settings.py#L685))
is correct and independent.
**Fix (refined — the audit's "gate on `exchange in {binance,delta}`" is a no-op since `exchange` is
always one of those):** gate the CEX-creds guard on **`twak_mode != "live"`** —
`if settings.enable_live_trading and settings.twak_mode != "live":`. The TWAK-live contest path
(`TWAK_MODE=live`) then skips the CEX requirement (the TWAK guard covers it), while the ICT/CEX live
path (`twak_mode=sim`, `exchange∈{binance,delta}`) still demands CEX creds. Verify the TWAK guard fires.

---

### Theme F — CMC x402 cross-audit vs the official docs

#### F1 · VERIFY · resend header `X-PAYMENT` vs docs' `PAYMENT-SIGNATURE` — *operator-gated, no code change*
Our code sends the **x402-v2 standard** `X-PAYMENT: base64(JSON{…})`; the docs show
`PAYMENT-SIGNATURE: <JWT>`. Read-only `402` probes returned `x402Version 2` + an `accepts[]` array,
which **matches the `X-PAYMENT` scheme we implement**, so the docs' form is most likely a simplified
representation. **But settlement has never actually succeeded** (0 receipts; off by default; wallet
unfunded). **Do NOT blind-change the header.** Before trusting receipts: fund the Base-USDC wallet,
run **one** real settled call, confirm a `200`; only if it `402`s again, capture `accepts[].extra` +
any `WWW-Authenticate` and adjust. Until then, treat "settled" counts as aspirational.

#### F2 · ENHANCE · `quotes/latest` is x402-payable too (we only use `dex/search`) — *worth, additive*
Probe (2026-06-09): `GET /x402/v3/cryptocurrency/quotes/latest` → `402`, `x402Version 2`, with the
**exact** Base-USDC eip3009 `$0.01` accept our `pick_accept()` already selects. Paying per-call for
**real CMC price quotes via x402** is a markedly stronger "Best Use of CoinMarketCap via x402" story
than dex-search enrichment, and it could feed the allocator's price/regime read directly.
**Fix (additive, off by default):** add a generic `quotes_latest(cmc_id|symbol)` reusing the existing
`fetch_x402()` loop (no new signing code); optionally prefer it for the per-tick pillar-1 read.
Switching the live default on still needs funding + the F1 settlement check (operator-gated).
> **Memory correction:** "x402 only `dex/search` payable" is **stale** — update to "`dex/search`
> AND `cryptocurrency/quotes/latest` are x402-payable (Base USDC eip3009 $0.01); others unverified."

---

### Theme G — Reaction time / two-speed loop  *(NEW — from the team domain insight)*

> Team insight (group chat): *"drawdown = reaction time. Faster reaction → smaller drawdown — faster
> to flip direction, exit, or enter. Plan a two-speed setup: a **fast** loop watching open positions /
> stops / regime shifts in near-real-time, and a **slow** loop for the heavier analysis/decisions —
> react fast (exit/flip/enter) while filtering noise to avoid overtrading."*

This is a genuinely sharp lens, and it exposes a real architectural gap the backend audit didn't frame.

#### G1 · MEDIUM · the agent is SINGLE-SPEED — intraday drawdown is unreacted-to for ~24h
**Where:** [run_allocator.py:316-329](scripts/run_allocator.py#L316). The NAV-vs-HWM drawdown check
and the **sole** call to `emergency_flatten` live together at the **end** of the heavy `_tick`,
reachable only *after* the 8-token `fetch_4h(…,2500)` candle pull, stale guards, broker build and
live reconcile — so it **cannot fire on its own**. The live cron runs that tick **once per contest
day** ([live_tick.sh](scripts/live_tick.sh), single-shot, no `--loop`). Net: between two scheduled
ticks (~24h) an intraday NAV crash toward the **30% DQ line** — the contest's *binding* constraint —
is invisible to the agent. (The contest is long-only spot with **no SL/TP**, because AMM swaps have
no native stop, so the DD halt *is* the only intraday protection.)

#### G2 · MEDIUM · a decoupled fast DD-monitor is WORTH building (enhance)
The drawdown gate is the binding constraint, yet it runs at the **slowest** cadence in the system.
A lightweight **fast monitor** that *only ever flattens* adds protection with **zero overtrading /
whipsaw risk** — because `emergency_flatten` is **one-directional** (sells token→USDT, sets `halted`;
it never opens or flips a position), and once halted the heavy tick refuses to trade. Crucially,
**all the primitives already exist and are pure/reusable**: `broker.nav` (cheap — uses `client.price`,
not the heavy candle pull), `emergency_flatten`, atomic `load_state`/`save_state`, the per-mode
`flock`, and the **bad-price guard** that prevents a false-positive flatten.
**Fix (design):** a thin entrypoint (e.g. `run_allocator.py --dd-watch` or a tiny `dd_monitor.py`)
cron'd every ~5–15 min during the contest window that: reuses `_live_preflight`; acquires the **same**
per-mode `flock` (so it can never race the daily tick); `load_state`; reads prices and applies the
**same bad-price guard**; computes `nav = broker.nav(prices)`; reads the **persisted** `hwm` (never
recompute); and on `dd > dd_cap` calls `emergency_flatten` + `halted=True` + atomic `save_state` +
journals `DD_HALT`. It **must never open/flip a position**. Keep the heavy decision daily. *(A cheaper
alternative: just run the existing tick on a tighter `--loop`/`--interval-min` or a smaller
`alloc_rebal_bars` — reuses every guard automatically, but also re-runs the heavy decision + risks
overtrading; the decoupled flatten-only monitor is the better fit for the insight.)*

---

### Theme H — Dashboard honesty (FE misleading-green-on-fallback)  *(NEW)*

The dashboard is mostly honest (StatusBar flips to "demo snapshot"; PillarsPanel body shows "not
minted"/"policy off"). The gaps are a few surfaces that **don't consume the `live` freshness signal**
and so pulse green even when serving the committed static `snapshot.json` (the dead/cold-deploy case).

- **H1 · HIGH\* (cosmetic) ·** [LiveWalletCard.tsx:27-34](web/src/components/LiveWalletCard.tsx#L27) —
  the pulsing green "live" badge is a **static literal**; the component only receives `wallet`, never
  `live`. On the static fallback it shows a pulsing-green "live" $8.88 wallet while `StatusBar` (one
  row up) says "demo snapshot" — the two contradict. **Fix:** thread the hook's `live` flag in
  (already in scope at [MissionControl.tsx:16](web/src/components/MissionControl.tsx#L16)/`:49`); show
  "live" only when `live && wallet.ok`, else a muted "snapshot" badge.
- **H2 · HIGH ·** [PillarsPanel.tsx:119-122](web/src/components/PillarsPanel.tsx#L119) — the green
  "chain 56" link pill is rendered from `reachable/chain_ok/chain_id` **baked true in the committed
  snapshot**, with no freshness gating, so a dead API still lights it green. **Fix:** thread `live` in
  (from [MissionControl.tsx:40](web/src/components/MissionControl.tsx#L40)); mute the link/chain pill
  to "snapshot" when `!live`.
- **H3 · MED ·** [IdentityCard.tsx](web/src/components/IdentityCard.tsx) + footer
  [MissionControl.tsx:82-85](web/src/components/MissionControl.tsx#L82) — renders the **ERC-8004 badge
  + capability chips** and the copy *"mints + heartbeats its ERC-8004 identity gaslessly"* while
  `agent_id=0` (nothing minted, heartbeat dead — ties to **B1**). `profile()` is explicitly a key-free
  description of what *would* be registered. The sibling `PillarsPanel` already degrades to **"not
  minted"** off the same `agent_id` — so the fix is proven feasible. **Fix:** gate the badge/"minted"
  framing on `pillars.nodereal.agent_id > 0`; soften the verb to "declares / configured to mint" until
  a mint is persisted.
- **H4 · LOW (partial) ·** the footer strapline asserts "CMC (x402) reads · gasless heartbeat via
  MegaFuel/NodeReal" unconditionally, regardless of `cmc.x402_enabled` / `nodereal.sponsorable` /
  `heartbeat_enabled`. The per-card pills are honest; only this static strapline overstates. **Fix
  (optional honesty polish):** make the footer conditional on those flags (all already in the payload).
- **H5 · LOW (partial) ·** [StatusBar.tsx:59](web/src/components/StatusBar.tsx#L59) — "last tx Nd ago"
  grows in real time off the **frozen** snapshot ts, so a long-lived static deploy reads "last tx 30d
  ago" next to the "demo snapshot" badge. **Fix (optional):** when `!live`, age relative to `servedAt`
  or suppress the growing age. The demo badge already carries the load.

---

## 3. Reaction-time, quantified (why G is worth it)

| | Current (single-speed) | With a fast DD-monitor |
|---|---|---|
| DD check cadence | once / ~24h (inside the heavy daily tick) | every ~5–15 min (flatten-only) |
| Worst-case unreacted intraday drawdown | up to a full day of adverse move toward the 30% DQ | bounded by the monitor interval |
| Overtrading / whipsaw risk added | n/a | **none** — `emergency_flatten` only sells→USDT + halts; never opens/flips |
| New code | — | one thin entrypoint reusing `nav`/`emergency_flatten`/`load_state`/`flock`/bad-price guard |

---

## 4. Rejected as false-positive / not-worth-it  *(the worth-it-vs-hype honesty)*

Re-verification refuted these (kept here so the doc stays honest):

1. **`--max-usd` slippage cap (part of A1) → HYPE.** No such `twak swap` flag exists (only
   `--slippage`, which we *will* add). Position size is already capped upstream. *(Verified via
   `twak swap --help`.)*
2. **`x402_dex` "dead wiring" → HYPE.** The render path is fully implemented + **tested** and gated
   behind the intentional `X402_ENABLED=false` default; flip the flag and it renders live data. Not
   dead code — a documented off-by-default paid read. *(At most: hide the "last DEX" row when x402 is
   off — pure polish.)*
3. **Pillar-3 "compound green pill" (separate from H2) → NOT-A-BUG / optional polish.** The green pill
   is labeled "chain 56" (a literally-true connectivity sub-fact) and is paired with amber "policy off"
   + "not minted" + an instruction line — not a lone "pillar live" claim. Optional: recolor the link
   pill from neon-green to the existing **cyan** "reachable" color. Not required.
4. **"Fast-monitor design risk" → NOT-A-BUG (forward-looking).** Every guard it warns about
   (bad-price, flock, atomic state) **already exists and works**; it's design advice for the unwritten
   monitor, folded into **G2**'s fix spec.

---

## 5. Suggested priority order (when fixes are greenlit)

1. **A1 (slippage flag)** — only item that touches real money on every live swap. *(slippage only — no max-usd.)*
2. **G1+G2 (fast DD-monitor)** — tightens the **binding** constraint (30% DQ) at near-zero risk; the highest-leverage *new* item, and it answers the team's reaction-time insight directly.
3. **B1+B2+H3 (pillar-3)** — persist `agent_id` + log the heartbeat + stop the UI claiming a mint that didn't happen — un-bricks **and** de-overstates the BNB-SDK pillar for judging.
4. **A2, A3, A4, E1** — live-tick robustness under failure / clean-`.env` boot.
5. **H1, H2 (FE misleading-green)** — quick judge-honesty polish on the two green-on-fallback pills.
6. **C1, C2, H4, H5** — operator/visibility polish.
7. **D1, D2** — lock the safety behavior in tests.
8. **F2 (quotes/latest helper)** — strengthens pillar-1; **F1** is a verify-before-trust operator gate (no code).

---

## 6. Fix specifications (verified — ready to implement on go-ahead)

> Lane-tagged so backend and FE can land cleanly. New settings default to **safe / behavior-preserving**;
> the SIM path stays byte-identical. **No mainnet action.** F1 is the only operator-gated item.

| # | Lane | Files | Change (precise) |
|---|------|-------|------------------|
| **A1** | BE | [twak_client.py](src/ictbot/exec/twak_client.py), [settings.py](src/ictbot/settings.py), `.env.example` | Add `TWAK_SLIPPAGE_PCT` (default `1.0`) + `TWAK_SLIPPAGE_FLAG` (default `--slippage`, **verified** via `twak swap --help`). In `swap()` on `execute=True`, append `[flag, str(pct)]` only when the flag is non-empty (mirrors `twak_gasless_flag`). **No `--max-usd`** (flag doesn't exist; sizing is capped upstream). |
| **A2** | BE | [bsc_spot_live.py](src/ictbot/exec/bsc_spot_live.py) | In `emergency_flatten()`: collect failed legs and re-`swap` each ~3× with backoff before the `CRITICAL` log; best-effort-complete, never raise. |
| **A3** | BE | [run_allocator.py](scripts/run_allocator.py) | Wrap `prices = broker.prices()` in `try/except RuntimeError` → print reason + `return 2`. |
| **A4** | BE | [kill_switch.py](src/ictbot/runtime/kill_switch.py) | `from ictbot.settings import DATA_DIR, PROJECT_ROOT`; `KILL_SENTINEL = DATA_DIR/"KILL_SWITCH_ENGAGED"`, `ENV_FILE = PROJECT_ROOT/".env"`. |
| **B1** | BE | [register_agent.py](scripts/register_agent.py), [kill_switch.py](src/ictbot/runtime/kill_switch.py) | Expose public `rewrite_env_key`; after `register_identity()`: `aid = res.get("agentId"); if aid: rewrite_env_key("AGENT_ID", str(aid))` + print prominently. |
| **B2** | BE | [run_allocator.py](scripts/run_allocator.py) | Replace heartbeat `except: pass` with `except Exception as e: print(... heartbeat failed ...)`; optional `heartbeat_ok` journal key. |
| **C1** | BE | [run_allocator.py](scripts/run_allocator.py) | Set `x402_attempted`/`x402_failed` in the REBALANCE journal entry. |
| **C2** | BE | [x402_cmc.py](src/ictbot/data/x402_cmc.py) | One `log.warning` in `fetch_x402()` when `x402_enabled` but `available()` is false. |
| **D1** | BE | [tests/test_run_allocator_hardening.py](tests/test_run_allocator_hardening.py) | `test_live_tick_preflight_missing_wallet_pw` → `tick("live", …) == 2`. |
| **D2** | BE | [tests/test_trade_floor.py](tests/test_trade_floor.py), [run_allocator.py](scripts/run_allocator.py) | Mocked-failure test for `_ensure_trade_floor`; add `FLOOR_NUDGE_FAILED` journal event when `banked == 0`. |
| **E1** | BE | [settings.py](src/ictbot/settings.py) | Gate the CEX-creds guard on `settings.enable_live_trading and settings.twak_mode != "live"`; verify the TWAK guard still fires. |
| **G1/G2** | BE | new `scripts/run_allocator.py --dd-watch` (or `dd_monitor.py`), [live_tick.sh](scripts/live_tick.sh)/cron | Flatten-only fast monitor: reuse `_live_preflight`, the per-mode `flock`, `load_state`, the bad-price guard, `broker.nav`, persisted `hwm`, `emergency_flatten`, `DD_HALT` journal. Never open/flip. Cron ~5–15 min in the contest window. |
| **H1** | FE | [LiveWalletCard.tsx](web/src/components/LiveWalletCard.tsx), [MissionControl.tsx](web/src/components/MissionControl.tsx) | Thread `live`; show "live" badge only when `live && wallet.ok`, else muted "snapshot". |
| **H2** | FE | [PillarsPanel.tsx](web/src/components/PillarsPanel.tsx), [MissionControl.tsx](web/src/components/MissionControl.tsx) | Thread `live`; mute the link/chain pill to "snapshot" when `!live`. |
| **H3** | FE (+BE B1) | [IdentityCard.tsx](web/src/components/IdentityCard.tsx), [MissionControl.tsx](web/src/components/MissionControl.tsx), snapshot/identity copy | Gate ERC-8004 "minted/active" framing on `agent_id > 0`; soften "mints + heartbeats" → "declares/configured to". |
| **H4** | FE | [MissionControl.tsx](web/src/components/MissionControl.tsx) | Footer strapline conditional on `cmc.x402_enabled` / `nodereal.sponsorable` / `heartbeat_enabled`. |
| **H5** | FE | [StatusBar.tsx](web/src/components/StatusBar.tsx) | When `!live`, age "last tx" vs `servedAt` or suppress the growing age. |
| **F2** | BE | [x402_cmc.py](src/ictbot/data/x402_cmc.py) | Additive off-by-default `quotes_latest(...)` reusing `fetch_x402()`. |
| **F1** | — | — | **No code** — operator: fund + one settled call to confirm `X-PAYMENT` before trusting receipts. |

**Verification once implemented (no live/mainnet):**
`PYTHONPATH=src .venv/bin/python -m pytest -q -k "not real_integration"` (green; real-integration
stays skipped) · the new A2/A3/D1/D2/G tests · one `--mode sim` tick (journal row sane, no behavior
change) · `web/` builds (`tsc` exit 0; bundle from a clean `/tmp` path — the repo dir's `*` breaks
esbuild config-load) · `git status` matches the lane split.

---

## 7. Live-action runbook (documented — NOT executed)

Spends real mainnet gas/funds; left for the team to run deliberately. Current state
([web/public/snapshot.json](web/public/snapshot.json)): identity **not minted** (`agent_id: 0`),
`sponsorable: false`, `x402_enabled: false` — both pillars **code-ready but not yet lit**.

**Pillar 3 — ERC-8004 identity + gasless heartbeat**
1. `python scripts/register_agent.py --register` (needs `ENABLE_LIVE_TRADING=true` + TWAK creds + wallet pw) — mints the identity.
2. Capture the returned `agentId` → `AGENT_ID=<id>` in `.env` (manual today; **automatic after B1**).
3. `AGENT_HEARTBEAT_ENABLED=true` (requires `NODEREAL_API_KEY`, enforced by the boot guard).
4. On the NodeReal dashboard set the **MegaFuel sponsor policy** (whitelist the registry contract + the identity wallet) so `pm_isSponsorable → true`. Confirm via `python scripts/verify_nodereal.py`.

**Pillar 1 — CMC x402 paid data**
5. Fund the Base USDC pay wallet (`register_agent.py` prints the address; `$0.01`/call) → `X402_ENABLED=true` so ≥1 settled receipt lands. **(F1: confirm a real `200` before trusting "settled" counts.)**

**Go-live (contest window only)**
6. `ENABLE_LIVE_TRADING=true` + `TWAK_MODE=live` + `DASHBOARD_JOURNAL=live`; cron [live_tick.sh](scripts/live_tick.sh) (and the new **`--dd-watch` fast monitor**) for 2026-06-22 → 06-28 only; pre-contest keep [forward_tick.sh](scripts/forward_tick.sh) SIM.

---

## 8. Legacy note

[scripts/fire_test_order.py](scripts/fire_test_order.py) + [close_test_order.py](scripts/close_test_order.py)
are **Binance-testnet-futures** scripts (`BTC/USDT:USDT`, `reduceOnly`), not the BSC/TWAK contest path.
Safe (both refuse mainnet keys) but they exercise the old ICT/Binance broker. Treat as legacy; a
TWAK/BSC equivalent (small guarded USDT↔BNB round-trip) would be the contest-relevant smoke test.

---

## 9. Methodology & grounding

Round-3 was a multi-agent full-stack pass: 4 parallel dimension auditors (dashboard BE, React FE,
three-pillar e2e, reaction-time) → an **adversarial verify** stage that tried to refute every
candidate (8 confirmed, **3 rejected**, §4) → 22 areas affirmatively **verified-solid**. Load-bearing
facts re-checked against the real tree: `twak swap --help` (slippage flag real, no max-usd);
`schemas.py ↔ types.ts` field-by-field (zero drift, `tsc` exit 0); `onchain.py` Multicall3 +
Chainlink path; `verify_paymaster_link` RPC; the DD-halt's single caller + once-daily cadence; the
`agent_id=0 → heartbeat no-op` chain. Backend Themes A–F carry over from rounds 1–2 with current
line numbers; G and H are new this round.
