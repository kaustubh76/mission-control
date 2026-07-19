# Implementation Audit — BNB Hack CMC

**Date:** 2026-06-12 · **9 days to submission** (2026-06-21) · **10 days to trading window** (2026-06-22 → 06-28)
**Branch:** `feat/implementation` @ `56e9843` + ~47 uncommitted files
**Scope:** full-repo audit for half-finished / unwired work — Python agent core, web dashboard, deploy + ops + docs, submission readiness.
**Method:** three parallel deep code sweeps (agent core · dashboard · deploy/docs), independent re-verification of every critical claim, plus live evidence runs (`pytest`, web build, Makefile dry-runs). Every finding below carries a file reference.

---

## §1 Executive verdict

**The implementation is complete and correctly wired; the local execution environment and the submission packaging are not.** The locked strategy (long-only spot momentum allocator, regime-adaptive cap, TWAK-signed, three Track-1 pillars) is wired end-to-end from data fetch through ranking, rebalance, journal, and on-chain heartbeat, with zero TODO/FIXME/stub markers in active code paths, zero orphaned dashboard components, and all Makefile targets resolving. Two classes of problem remain. **(1) Environment:** the venv's editable install points at the *old* `Rahul_ideation` repo, so `make test` / `make run_allocator` / `make register_agent` currently execute the diverged upstream tree, not this repo (finding C4 — proven by 21 pytest collection errors; pinned to local `src` the suite runs 1104 passed / 2 failed, and both failures are a time-bombed legacy test, M6). **(2) Packaging:** the README still describes the *previous product* (ICT perp scalper), five submission docs planned in [bnb_hackathon_plan.md](bnb_hackathon_plan.md) §7/§12 don't exist yet (`DEMO.md`, `SUBMISSION.md`, `docs/strategy.md`, `docs/twak_integration.md`, `docs/x402_receipts.md`), the entire dashboard refactor is uncommitted, and the 2026-06-08 on-chain registration lacks a captured proof pack. C4 is a 10-minute fix but must land before any further `make`-driven run; the rest are packaging/ops tasks that fit in the 9 remaining days yet would directly cost prize points if shipped as-is.

---

## §2 End-to-end wiring trace (Python agent core)

### 2.1 Allocator runtime — VERIFIED ✅

Entry point: [scripts/run_allocator.py](../scripts/run_allocator.py). Full live flow traced:

| Step | Where | Status |
|---|---|---|
| 4h candles, CMC → Binance fallback, all 8 contest tokens | `run_allocator.py:279` (`fetch_4h`) | ✅ |
| Close-matrix alignment | `run_allocator.py:280` (`align_close_matrix`) | ✅ |
| Fear & Greed → regime score | `run_allocator.py:294` (`fear_greed`) | ✅ |
| Regime-adaptive target weights (cap ∈ [0.40, 0.85]) | `run_allocator.py:365-373` → [src/ictbot/strategy/regime_score.py](../src/ictbot/strategy/regime_score.py) `adaptive_target_weights()` | ✅ |
| Rebalance via TWAK broker | `run_allocator.py:430` → `broker.rebalance(target, prices)` | ✅ |
| Drawdown halt + emergency flatten | `run_allocator.py:412-427` (`dd > dd_cap` → `emergency_flatten`) | ✅ |
| Journal (NAV, fees, swaps, rationale) | `run_allocator.py:525` | ✅ |
| ERC-8004 heartbeat per tick | `run_allocator.py:529-534` → [src/ictbot/agent/identity.py](../src/ictbot/agent/identity.py) `write_heartbeat()` | ✅ |

SIM/LIVE state separation (`allocator_journal.jsonl` / `allocator_state.json`, HWM, cumulative swap counter): `run_allocator.py:66-81`. Live reconciliation against on-chain balances (`_reconcile_live`, 2% tolerance, logs `RECON_DRIFT`, observation-only): `run_allocator.py:389`.

### 2.2 ta_rank lever (commit `56e9843`) — VERIFIED IN THE LIVE PATH ✅

The proven TA-PnL lever is genuinely in the live ranking decision, not just the backtest:

- **Live call:** `run_allocator.py:320-361` — when `ALLOC_TA_ENABLED=true`, fetches per-token TA scores from `cmc_agent_hub.token_ta_scores()` (CMC MCP), with a **local fallback** (`ictbot.strategy.technicals.token_ta_score` on daily-resampled candles) when the MCP read fails, and neutral treatment when both are unavailable — no crash path.
- **Ranking integration:** [regime_score.py:302-310](../src/ictbot/strategy/regime_score.py) — `ta_token_scores` + `w_ta_rank > 0` routes through `_weights_at_ranked(..., ta_score=ta_mat, w_ta_rank=w_ta_rank)`; otherwise the untilted `_weights_at` path runs.
- **Gating:** `ALLOC_TA_ENABLED` (default false), `ALLOC_TA_W_RANK` (default 1.0) in [settings.py](../src/ictbot/settings.py).
- **Test:** `tests/test_cmc_agent_hub.py::test_token_ta_scores_per_token`.
- **Backtest companion** for A/B: `weight_path_tilted()` in `momentum_allocator.py`.

### 2.3 Pillar 1 — CMC Agent Hub — VERIFIED ✅ (real plumbing, flag-gated)

- **Data MCP:** [src/ictbot/data/cmc_agent_hub.py](../src/ictbot/data/cmc_agent_hub.py) — MCP tools, TTL-cached, never raises; usage telemetry journaled to `data/cmc_mcp_usage.json`. Gate: `CMC_MCP_ENABLED`.
- **x402 paid data:** [src/ictbot/data/x402_cmc.py](../src/ictbot/data/x402_cmc.py) — **real EIP-3009 USDC signing on Base (chain 8453)** via `bnbagent.X402Signer`, not mocked; receipts to `data/x402/receipts.json`; per-call cap + session budget enforced. Gate: `X402_ENABLED`. Used in live loop for DEX enrichment (`run_allocator.py:476-491`).
- **Skills pipeline:** `market_overview()` (`cmc_agent_hub.py:261-299`) composes TA breadth + global metrics + F&G into a risk budget; gate `CMC_SKILL_REGIME`; consumed at `run_allocator.py:336-345`.
- Every CMC read degrades to `None` on failure → no tick aborts. Tests: `tests/test_cmc_agent_hub.py` (6), `tests/test_x402_cmc.py`, `tests/test_cmc_client.py`.

### 2.4 Pillar 2 — TWAK execution — VERIFIED ✅

- **Broker:** [src/ictbot/exec/bsc_spot_live.py](../src/ictbot/exec/bsc_spot_live.py) `TwakSpotBroker` — sell-overweight-first rebalancing, `emergency_flatten()`, NAV/holdings/weights reads.
- **Client:** [src/ictbot/exec/twak_client.py](../src/ictbot/exec/twak_client.py) — SIM client (fees+slippage modeled) and `CliTwakClient` live subprocess (`twak swap --slippage 1 --max-usd <cap>`, JSON-parsed `SwapResult`, no blind retries).
- **Guards:** `ENABLE_LIVE_TRADING=false` raises `LiveTradingDisabled` at construction; `MIN_REBAL_FRAC=0.02`, `MIN_SWAP_USD=1.0`, per-swap `max-usd` cap. Gasless via MegaFuel when `TWAK_GASLESS=true`.
- Tests: `tests/test_bsc_spot_live.py` (8 — rebalance, flatten, slippage, failure modes).

### 2.5 Pillar 3 — BNB AI Agent SDK identity — VERIFIED ✅

- **Mint + contest registration:** [scripts/register_agent.py](../scripts/register_agent.py) — ERC-8004 mint via `bnbagent.register_identity()` (gasless MegaFuel), then `twak compete register --json`; persists `AGENT_ID` to `.env`.
- **Heartbeat:** `identity.py` `write_heartbeat()` — NAV + rationale to ERC-8004 metadata every rebalance tick; best-effort (never aborts a tick); gated by `AGENT_HEARTBEAT_ENABLED` + non-zero `AGENT_ID`. Gasless routing prefers keyed NodeReal endpoint (`identity.py:54-82`).
- **NL strategy spec:** [src/ictbot/agent/strategy_spec.py](../src/ictbot/agent/strategy_spec.py) (the agent "talks").

### 2.6 Code-hygiene sweep — CLEAN ✅

`grep -r "TODO|FIXME|XXX|HACK|NotImplementedError|placeholder|coming soon"` across `src/` and `scripts/`: **zero hits in active code paths** (remaining "stub"/"mock" hits are legitimate comments in upstream perp modules and test-double language). No hardcoded secrets found. `.env` / `.env.local` are **not** git-tracked (`git ls-files` empty).

---

## §3 Dashboard wiring matrix (web/)

Entry: `main.tsx` → `ErrorBoundary` → `App` → [MissionControl.tsx](../web/src/components/MissionControl.tsx). **All 17 panels + 8 ui primitives are imported and rendered — zero orphans**, including every untracked new file (`HeroRow`, `ui/Collapsible`, `ui/ErrorBoundary`, `ui/Sparkline`, `ui/Stat`, `ui/StatusPill`, `ui/Tooltip`, `lib/glossary`, `lib/pnl`).

Single data source: `useAllocator()` polls **`/api/snapshot`** every 4 s, falling back to static `web/public/snapshot.json` (the Vercel-offline path). Producer side confirmed in [src/ictbot/api/reads.py](../src/ictbot/api/reads.py):

| Component(s) | Snapshot field | Producer (`api/reads.py`) |
|---|---|---|
| HeroRow, PnLCard, EquityCurve, NavCard | `nav` | `nav_card()` |
| StatusBar, HeroRow, ControlPanel | `health` | `health_card()` |
| HeroRow, RegimeDial, MarketIntelPanel | `regime` | `regime_card()` |
| RebalanceTable, RationaleTicker | `rebalances`, `rationale` | `rebalances_card()`, `rationale_card()` |
| PillarsPanel | `pillars` | `pillars_card()` |
| LiveWalletCard | `wallet` | `wallet_card()` |
| MarketIntelPanel, CmcApiCard | `market_intel`, `cmc_api` | `market_intel_card()`, `cmc_api_card()` |
| **CmcAgentHubPanel** (commit `d9447fd`) | `agent_hub` | `agent_hub_card()` (`reads.py:442-464`) ← `cmc_agent_hub.telemetry()` + journal `cmc_skill`/`ta_health` + x402 receipts |

Other checks: ErrorBoundary at root **and** per panel (`MissionControl.tsx:23-29` `Panel()` wrapper) ✅ · 24-term glossary surfaced via `InfoTip` in 40+ places ✅ · fonts/title/meta consistent across `index.html` / `tailwind.config.js` ✅ · vite `/api` proxy → `:8000` matches `make api` ✅ · `scripts/build_web.sh` stages in `/tmp` (space-in-path workaround), refreshes `snapshot.json` pre-build ✅ · no placeholder/lorem/mock data (the hardcoded "PAPER · $1K" pills are intentional mode labels) ✅.

---

## §4 Deploy topology — CONSISTENT ✅ (two stale leftovers, see §5)

- **Render = API** ([render.yaml](../render.yaml)): deploys `feat/implementation` via [infra/Dockerfile.dashboard](../infra/Dockerfile.dashboard), installs `[api,bnb]` extras only, reseeds from `infra/seed/allocator_journal.jsonl` when `API_SEED_ON_START=1`, `TWAK_MODE=sim`, `CMC_API_KEY` correctly `sync: false`.
- **Vercel = static SPA** ([vercel.json](../vercel.json) + [.vercelignore](../.vercelignore)): builds `web/` only → `web/dist`; Python app fully excluded.
- **Two-source freshness pattern** (Render seed + SPA fallback snapshot) is real and documented in the `refresh_dashboard` Makefile target.
- **.env hygiene:** `.env` / `.env.local` untracked ✅. All 77 `.env.example` vars map to real `settings.py` fields (no dead example vars) ✅.

---

## §5 Findings — sorted by severity

### 🔴 Critical (would cost prize points if shipped as-is)

| # | Finding | Evidence | Fix | Effort |
|---|---|---|---|---|
| C1 | **README is the legacy product.** Opens "ICT AI BOT PRO MAX", describes perp scalping on Bybit/Delta/Binance + Streamlit, setup path is `/Users/apple/Desktop/Rahul_ideation`. Only one BNB-relevant line (architecture.svg link, `README.md:158`). Judges reading the public repo see the **wrong strategy and the wrong product**. | [README.md:1-80](../README.md) | Rewrite around the momentum allocator + 3 pillars: 90-sec pitch, architecture.png, setup, on-chain proof links, demo link. The correct narrative already exists in `bnb_hackathon_plan.md` §TL;DR and `bnb_strategy_decision.md` — mostly assembly. | ~2-3 h |
| C2 | **Five planned submission docs missing:** `DEMO.md`, `SUBMISSION.md`, `docs/strategy.md`, `docs/twak_integration.md`, `docs/x402_receipts.md` — all named in [bnb_hackathon_plan.md](bnb_hackathon_plan.md) §7 repo layout and §12 deliverables; `twak_integration.md` + `x402_receipts.md` are explicitly **TWAK special-prize artifacts** (35 of 100 rubric points). | `ls` confirms none exist | Timeline already slots this at Day 15 (Jun 18) — keep that slot, don't let it slip. x402_receipts.md can be generated from `data/x402/receipts.json`. | ~1 day |
| C3 | **Submission deadline ambiguity:** plan says **Jun 21 17:30 UTC** (`bnb_hackathon_plan.md:101`), but the scraped hackathon page says **"Submission lock - June 21st · 12:00pm UTC"** ([checkings.md](checkings.md) §2). 5.5 h difference. | both files | Verify on DoraHacks / hackathon TG; plan for the **earlier** time (12:00 UTC). | 10 min |
| C4 | **The venv imports `ictbot` from the OLD repo.** The editable install (`.venv/.../__editable__.ictbot-0.1.0.pth`) points at `/Users/apple/Desktop/Rahul_ideation/src`. Some Makefile targets pin `PYTHONPATH=src` to compensate (`api`, `snapshot`, `refresh_dashboard` — the comment at `Makefile:12` shows this was known), but **`make test`, `make run_allocator`, `make validate_allocator`, `make register_agent`, `make forward_report`, `make ab_regime`, `make verify_nodereal` do NOT** (`Makefile:84-115`), and neither the scripts nor `tests/conftest.py` bootstrap `sys.path`. Proof: bare `pytest -q` dies with **21 collection errors**, all `ImportError ... from /Users/apple/Desktop/Rahul_ideation/src/ictbot/...` — i.e. these targets execute the *diverged upstream tree*, not this repo. Running the contest agent or on-chain registration through `make` would run the wrong code. | §6 evidence; `Makefile:7-8, 84-115` | One command: re-run the editable install from this repo (`.venv/bin/pip install -e ".[dev,bnb,api]"`), and/or add `PYTHONPATH=src` to every venv-using target for belt-and-braces. | 10 min |

### 🟡 High (submission readiness)

| # | Finding | Evidence | Fix | Effort |
|---|---|---|---|---|
| H1 | **~47 uncommitted files** — the entire dashboard refactor (17 modified components), 9 new untracked web files, architecture diagram rework, doc updates. Render deploys from `feat/implementation`, so **the deployed dashboard ≠ what's running locally** until these land. | `git status` | Commit in logical chunks (web refactor / diagrams / docs) once the build evidence in §6 is green. | ~1 h |
| H2 | **On-chain registration done (2026-06-08) but un-evidenced.** Per [bnb_strategy_decision.md](bnb_strategy_decision.md) (🟢 LIVE ON-CHAIN callout) the ERC-8004 identity is minted (agentId 1313) and the wallet contest-registered (participant `0xE8A3…6215`) — but no artifact file exists: `register_agent.py` writes nothing to disk except `AGENT_ID` into `.env`, and the plan's `data/compete/registration.json` was never implemented. Submission needs the proof pack (tx hashes, `isRegistered=true` read, screenshots). **➜ RESOLVED 2026-06-12:** registration re-verified on-chain (`isRegistered=true`); the 1313 identity proved unrecoverable (owned by an unrelated wallet) and was **re-minted as agentId 133085** from the pinned identity wallet — see the decision-record correction + `data/compete/identity_mint_2026-06-12.json`. | decision record header; `data/` listing | Re-verify via `twak compete status --json` + BscScan Read-Contract; hand-capture `data/compete/registration.json` + screenshots by Jun 19. | ~1 h |
| H3 | **`web3` undeclared but directly imported** in [src/ictbot/api/onchain.py:103](../src/ictbot/api/onchain.py) and [src/ictbot/data/x402_cmc.py:75](../src/ictbot/data/x402_cmc.py); it arrives only **transitively** via `bnbagent` (`[bnb]` extra). Render installs `[api,bnb]` so it works today, but a `bnbagent` dep change would silently break on-chain reads + x402. | [pyproject.toml](../pyproject.toml) (no `web3` anywhere) | Add `web3>=6` to the `[bnb]` (or `[api]`) extra. | 5 min |
| H4 | **`docker-compose.yml` dashboard service still launches Streamlit** (`:8501`, root Dockerfile) — the shipped UI is the React SPA on Vercel; compose is now misleading legacy local-dev config. | [docker-compose.yml:14-23](../docker-compose.yml) | Mark legacy in a comment, or repoint to `make api` + `web/dist`. Not deploy-blocking (Render uses `infra/Dockerfile.dashboard`). | 15 min |

### 🟢 Medium / hygiene (no blockers)

| # | Finding | Evidence | Fix |
|---|---|---|---|
| M1 | **22 settings fields undocumented in `.env.example`** — incl. behavior-tuning knobs `ALLOC_ADAPTIVE`, `ALLOC_CAP_FLOOR/CEILING`, `CONTEST_START/END`, `DASHBOARD_JOURNAL`, `TRADE_FLOOR_MIN/LOOKAHEAD_DAYS`, `ONCHAIN_READS_ENABLED`. All have sane defaults; operators just can't discover them from the template. | `settings.py` vs [.env.example](../.env.example) | Append a documented block to `.env.example`. |
| M2 | **8 orphaned scripts** unreferenced by any Makefile target or doc: `archive_journal.py`, `close_test_order.py`, `fire_test_order.py`, `gen_architecture.py`, `probe_agent_hub.py`, `probe_cmc.py`, `verify_wallet_parity.py`, `wfo_gates_ab.py`. Plan §12 says "no cruft" in the public repo. | `scripts/` | Delete, or add a "debug utilities" note to README/operations. |
| M3 | **`_ensure_trade_floor` has no dedicated test** — the 7-trade contest-minimum nudge (`run_allocator.py:433-448`) is functional and non-fatal on underfunding, but untested, and the nudge size (`max(min_swap_usd*1.5, 2.0)`) is hardcoded. This guard is what stands between a quiet week and a DQ. | `run_allocator.py:433-448` | Add a unit test (cumulative swaps < 7 near deadline → nudge fires; insufficient USDT → `FLOOR_NUDGE_FAILED` logged). |
| M4 | **Upstream perp modules ride along** in the submission repo (binance/delta brokers, ICT scanner, Streamlit UI) — intentional provenance per plan, but adds reviewer noise. The pyproject description still reads "ICT-style scalping signal engine for crypto perpetuals". | `pyproject.toml:7` | At minimum update the pyproject `description` when fixing C1. |
| M5 | Uncommitted [src/ictbot/notify/signal_check.py](../src/ictbot/notify/signal_check.py) change is a benign doc-path fix (`architecture.excalidraw` → `archive/architecture_ictbot_upstream.excalidraw`). Deleted `docs/architecture_bnb.excalidraw` is referenced nowhere — safe deletion. | `git diff` | Commit with H1. |
| M6 | **2 time-bombed tests fail since ~Jun 4:** `test_news_alert.py` dedup tests pin a fake now of 2026-05-28 while `_save_alerted` prunes against the real wall clock (`news_alert.py:63`, `PRUNE_AFTER_DAYS=7`) — the dedup entry is pruned on save, so the tests can never pass again. Product code is fine for current events; legacy notify stack, not the allocator path. But plan §12 requires "`make test` passes". | §6 evidence; [tests/test_news_alert.py:27,79,93](../tests/test_news_alert.py) | Make the test's fake now relative (`datetime.now(tz)+...`) or inject the clock into `_save_alerted`. |

---

## §6 Test & build evidence

> Run on 2026-06-12 as part of this audit.

- **Makefile dry-run** — `make -n validate_allocator run_allocator forward_report refresh_dashboard register_agent test api`: **all 7 expand OK** ✅

- **Python test suite, as `make test` runs it** (bare `pytest -q`): ❌ **21 collection errors in 1.5 s** — every error an `ImportError ... from /Users/apple/Desktop/Rahul_ideation/src/ictbot/...`. This is finding **C4** (stale editable install), not a code defect: the suite never reached this repo's source.

- **Python test suite pinned to this repo** (`PYTHONPATH=src .venv/bin/python -m pytest -q`):

  ```
  2 failed, 1104 passed, 9 skipped, 1 warning in 57.54s
  ```

  - 9 skips are intentional live-integration opt-ins (`RUN_X402_INTEGRATION=1`, `RUN_X402_SETTLE=1`, etc.) ✅
  - The 2 failures (`tests/test_news_alert.py::test_deduplicates_within_a_run`, `::test_deduplicates_across_processes`) are a **time-bombed test, not a product regression**: the tests pin a fake now of **2026-05-28** (`test_news_alert.py:27`), while `_save_alerted` prunes dedup entries against the **real wall clock** ([src/ictbot/notify/news_alert.py:63](../src/ictbot/notify/news_alert.py) — `datetime.now(timezone.utc) - 7 days` = 2026-06-05). The May-28 entry is pruned the moment it is saved, so dedup never persists and 3 alerts fire instead of 1. The tests passed until ~Jun 4 and will fail forever after. Dedup behaves correctly for genuinely current events; module is the legacy notify stack, not the allocator path. Logged as **M6**.

- **Web build** (`bash scripts/build_web.sh` → snapshot refresh + `tsc && vite build`): ✅ **green** — 1215 modules transformed, `web/dist` written, type-check passed over the entire uncommitted dashboard refactor. (Cosmetic: single 750 kB JS chunk triggers Vite's >500 kB warning — fine for a hackathon SPA.)

- **Stale `__pycache__` from previous repo locations:** compiled caches carried over from the `Rahul_ideation` copy (and an earlier `BNB Hack * CMC` path with spaces — the same path `scripts/build_web.sh` works around) make pytest tracebacks display old absolute paths. Cosmetic, but worth a `find . -name __pycache__ -exec rm -rf {} +` alongside the C4 fix.

---

## §7 Submission-readiness checklist (vs plan §12, 9 days out)

| Deliverable | Status | Where / what's left |
|---|---|---|
| Public repo, tests green | 🟡 | 1104/1115 pass when pinned to local src (§6); but `make test` itself is broken by C4, and 2 time-bombed tests (M6) need a fix; repo mid-refactor (H1) |
| README 90-sec pitch + architecture + setup + proof | 🔴 | C1 — legacy content |
| Architecture diagram | ✅ | `docs/architecture.svg` + `.png`, regenerated; old diagram archived |
| `.env.example` complete, no secrets | 🟡 | M1 (22 undocumented vars); no secrets leaked ✅ |
| On-chain: registered agent address + tx proof | 🟡 | Registration verified on-chain 2026-06-12 (`isRegistered=true`, participant `0xE8A3…6215`); live identity = **agentId 133085** (re-minted 06-12; 1313 was unrecoverable). Mint proof captured (`data/compete/identity_mint_2026-06-12.json`); registration screenshots still to capture (H2) |
| DoraHacks text (`SUBMISSION.md`) | 🔴 | C2 — missing |
| Demo video + script (`DEMO.md`) | 🔴 | C2 — missing (record slot: Jun 20) |
| TWAK special artifacts (`twak_integration.md`, `x402_receipts.md`, receipts JSON) | 🔴/🟡 | Docs missing (C2); receipts plumbing real and logging ✅ |
| Forward paper validation running daily | ✅ | `make forward_report`; Render journal reseeded with 6/10 forward PnL (commit `4c4e2d1`) |
| No token launches / DQ behaviors | ✅ | n/a |

**Suggested order for the remaining 9 days:** fix the venv editable install (C4, 10 min, blocks everything `make`-driven) → commit the refactor (H1) → fix README + pyproject description (C1/M4) → write the five docs (C2) → confirm true deadline (C3) → add `web3` dep + `_ensure_trade_floor` test (H3/M3) → verify the 06-08 registration + capture the proof pack by Jun 19 (H2) → record demo Jun 20 → submit **before Jun 21 12:00 UTC**. The full step-by-step execution plan lives in [docs/remediation_plan.md](remediation_plan.md).

---

## §addendum 2026-06-13 — CMC MCP + Skills-Marketplace integration: real, visible, honest

**Problem (user-reported):** "no sense of wiring in the product." Root cause was NOT half-written
code — the CMC MCP/Skills/x402 layer was built and tested, but (a) the dashboard panel that exhibited
it was **deleted** in `3aac1c2` because it rendered all-null when flags were off, and (b) only **3 of
CMC's 12 MCP tools** were used, feeding a single cap nudge. The composed `market_overview()` was also
mislabeled as "the Skills Marketplace."

**Done (this branch):**
- **Truth probe** — `scripts/probe_agent_hub.py` now records the live `tools/list` (12 tools, with
  input schemas) **and** probes the Skills-Marketplace endpoints. Evidence: `/skills*` → 404,
  `skills.coinmarketcap.com` → no DNS; only the 12 Data-MCP tools are callable. So the composed skill
  is honestly labeled `skill_source="composed"` (set in [data/cmc_agent_hub.py](../src/ictbot/data/cmc_agent_hub.py)).
- **8 of 12 tools now drive decisions** (was 3) — added `quotes_latest` (+ `verify_cmc_ids`: 8/8 IDs
  verified live), `derivatives_stress` (leverage/funding **brake**), `macro_events`/`next_macro_event`
  (**de-risk guard** into CPI/FOMC), `mktcap_technical_analysis` (global regime term), `latest_news`.
  Each is its own A/B flag (default OFF), never raises, and only ever **lowers** the cap in fragile
  conditions → DQ-safety-positive. Flags: `CMC_MKTCAP_TA`, `CMC_DERIV_BRAKE`, `CMC_MACRO_GUARD`,
  `CMC_QUOTES_XCHECK`, `CMC_NEWS_ENABLED` (see [settings.py](../src/ictbot/settings.py), `.env.example`).
- **Product surface revived** — recreated [web/src/components/CmcAgentHubPanel.tsx](../web/src/components/CmcAgentHubPanel.tsx)
  with a clean disabled state (the original removal reason), a per-tool call breakdown (proves which
  of the 12 tools ran), the composed-skill read (regime, risk budget, derivatives, macro, news, quotes),
  a `skill_source` truth badge, and x402 receipts. Wired into Tier-B of [MissionControl.tsx](../web/src/components/MissionControl.tsx).
- **Contract** — `AgentHubSkill`/`AgentHubOut` extended ([api/schemas.py](../src/ictbot/api/schemas.py)),
  surfaced in `agent_hub_card` ([api/reads.py](../src/ictbot/api/reads.py)), mirrored in
  [web/src/api/types.ts](../web/src/api/types.ts), and exposed at a dedicated `GET /api/agent-hub`.
- **Enabled in deploy** — `.env` + `render.yaml` + `.env.example` turn the layer on (incl. x402).

**Evidence:** live SIM tick calls 8 tools / 15 MCP calls; Gate-A re-validated with all flags on —
**worst-week DD 17.5%** (≤25% ceiling, ≤30% DQ), **15.3 trades/wk** (≥7). Tests:
`tests/test_cmc_agent_hub.py` (17 cases incl. brake/guard/ID-resolution), strategy-registry +
acceptance-gate + api-reads green; `web` tsc + vite build clean.
