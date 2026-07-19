.PHONY: install test coverage api api_commerce scan signal_check news_alert backtest best sweep sweep_campaign wfo wfo_all validate_trend validate_allocator validate_strategy forward_promote campaign stability sweep_arms forward_track forward_track_report forward_track_all readiness playbook validate_all sim_test_all cmc_check mcp_check probe_agent_hub ab_regime run_allocator forward_report register_agent verify_nodereal remint_identity snapshot refresh_dashboard deploy_dashboard heartbeat_check commerce_wallet commerce_job commerce_serve commerce_serve_check commerce_serve_daemon commerce_settle wfo_per_pair smoke_gate smoke_pairs pair_readiness status edge_check session_report size kelly ror scoreboard journal bias_compare bias_scoreboard bt_curve architecture clean

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -q -U pip && pip install -q -e ".[dev,api,bnb,tg]"

test:
	. .venv/bin/activate && PYTHONPATH=src python -m pytest -q

# Branch coverage of the PnL-campaign code paths (run_allocator profit-lock/daily-floor,
# the sweep decision-driver, the API surfacing) + the strategy-validation campaign and the
# stability harness (the test-hardening campaign). Needs pytest-cov (in the [dev] extra).
coverage:
	. .venv/bin/activate && PYTHONPATH=src python -m pytest -q --cov=scripts --cov=src/ictbot/api \
		--cov-branch --cov-report=term-missing \
		tests/test_profit_lock.py tests/test_profit_lock_lifecycle.py \
		tests/test_profit_lock_properties.py tests/test_sweep_campaign.py \
		tests/test_daily_floor.py tests/test_api_reads.py \
		tests/test_run_allocator_hardening.py tests/test_trade_floor.py \
		tests/test_strategy_campaign.py tests/test_strategy_stability.py \
		tests/test_forward_promote.py tests/test_forward_machinery.py \
		tests/test_campaign_properties.py tests/test_forward_properties.py \
		tests/test_contest_safety.py tests/test_strategy_aliases.py \
		tests/test_performance.py tests/test_playbook_parity.py \
		tests/test_sim_test_all.py tests/test_cmc_check.py tests/test_mcp_check.py

# React "Mission Control" dashboard backend (read-only FastAPI + guarded sim
# controls). Needs the api extra:  python -m pip install -e ".[api]"
# PYTHONPATH=src pins the LOCAL ictbot (this venv otherwise resolves ictbot to a
# sibling repo). API_DEV_CORS lets the Vite dev server (:5173) call it cross-origin.
#   make api                  → http://localhost:8000  (Swagger at /docs)
#   cd web && npm run dev     → http://localhost:5173  (proxies /api → :8000)
api:
	. .venv/bin/activate && PYTHONPATH=src API_DEV_CORS=1 python -m uvicorn ictbot.api.app:app --host 0.0.0.0 --port 8000 --reload

# KEYED operator dashboard — run the API WITH the ERC-8183 commerce signing keys so the dashboard's
# "Create Job" button works end-to-end (one click → the real bsc-mainnet create→register→budget→
# fund→serve→settle loop, no wallet popups). Builds the SPA first so the API serves it at
# http://localhost:8000 (open that, NOT :5173). Keys are read from .env / ~/.bnbagent and stay LOCAL
# — never the cloud. The public Vercel dashboard remains read-only (can_create=false).
#   make api_commerce      # then open http://localhost:8000 and click "Create + serve a real job"
api_commerce:
	( cd web && npm run build )
	@echo "→ open http://localhost:8000  (the 'Create Job' button is live here; the keys stay LOCAL)"
	. .venv/bin/activate && PYTHONPATH=src API_DEV_CORS=1 \
	  ERC8183_ENABLED=true ERC8183_NETWORK=bsc-mainnet ERC8183_STORAGE=ipfs \
	  STORAGE_API_KEY="$$(grep '^JWT_SECRET=' .env | cut -d= -f2-)" \
	  CLIENT_WALLET_PASSWORD="$$(cat $$HOME/.bnbagent/buyer-main.pass)" \
	  CLIENT_WALLET_DIR="$$HOME/.bnbagent/buyer-main" \
	  python -m uvicorn ictbot.api.app:app --host 0.0.0.0 --port 8000

scan:
	. .venv/bin/activate && python -m ictbot.orchestrator.scanner

# One-shot Telegram ping: per-pair signal cards (PAIR / 4H BIAS / ... / TP1-3).
#   make signal_check               → sends to Telegram (minimal cards only)
#   make signal_check DRY=1         → prints to stdout (no network)
#   make signal_check FULL=1        → also append the canonical robustness checklist
DRY ?=
FULL ?=
signal_check:
	. .venv/bin/activate && python -m ictbot.notify.signal_check \
		$(if $(DRY),--dry-run,) $(if $(FULL),--full,)

# Standalone news-aware Telegram alert. Fires ONCE when a high-impact macro
# event enters WINDOW minutes ahead. Dedup is persisted in data/journal/.
#   make news_alert                 → check + alert (default 60-min window)
#   make news_alert WINDOW=30       → tighter window
#   make news_alert DRY=1           → preview to stdout, no Telegram
#   make news_alert RESET=1         → wipe the dedup store (alert again next run)
WINDOW ?= 60
RESET ?=
news_alert:
	. .venv/bin/activate && python -m ictbot.notify.news_alert \
		--window-min $(WINDOW) $(if $(DRY),--dry-run,) $(if $(RESET),--reset,)

# Default to BTC, override:  make backtest PAIR=ETH/USDT:USDT BARS=5000
PAIR ?= BTC/USDT:USDT
BARS ?= 5000
backtest:
	. .venv/bin/activate && python -m ictbot.engine.backtest "$(PAIR)" --bars $(BARS) --verbose

# Best-known config so far on BTC (5000 1m bars):
# poi_tol=0.005, sl=0.003, tp=0.009 (1:3 RR), no FVG, --invert
# 6 signals, 3W/3L = 50% win rate, +1.0R expectancy
best:
	. .venv/bin/activate && python -m ictbot.engine.backtest "$(PAIR)" --bars $(BARS) \
		--poi-tol 0.005 --no-fvg --sl 0.003 --tp 0.009 --invert

# Default to quick (16 combos); override:  make sweep PAIR=ETH/USDT:USDT BARS=1000
sweep:
	. .venv/bin/activate && python -m ictbot.engine.sweep "$(PAIR)" --bars $(BARS) --quick

# Sweep every pair in PAIRS and print scoreboard
scoreboard:
	. .venv/bin/activate && python -m ictbot.engine.sweep --all --bars $(BARS) --quick

# Walk-forward optimization (train + out-of-sample test)
wfo:
	. .venv/bin/activate && python -m ictbot.engine.wfo "$(PAIR)" --bars $(BARS) --quick --invert

# Cross-pair WFO — which pairs have an edge that holds out-of-sample?
wfo_all:
	. .venv/bin/activate && python -m ictbot.engine.wfo --all --bars $(BARS) --quick --invert

# Trend-following Gate A on real 4h basket data (the strategy-switch validation).
# Prints per-asset verdicts + the portfolio PASS/FAIL decision at two friction levels.
# NOTE: this FAILED on the contest universe (no edge) — kept as the audit trail that
# led to the momentum allocator. See `make validate_allocator`.
validate_trend:
	. .venv/bin/activate && python scripts/validate_trend.py $(ARGS)

# Strategy validation: the committed momentum allocator over ALL
# rolling 7-day windows on the 8-token universe. Prints the return/DD distribution
# + the DQ-safe / active gate verdict at two friction levels. Override the risk dial
# with ARGS="--cap 0.40".
validate_allocator:
	. .venv/bin/activate && PYTHONPATH=src python scripts/validate_allocator.py $(ARGS)

# Multi-day P&L curve for the CMC-native arm (momentum_cmc): replays its decisions over the
# accumulated CMC DAILY candles (CEX-free) at the LIVE config and prints the equity curve, the
# contest-week range, best/worst week, recent slices — plus the REAL forward-journal P&L so far.
# Read-only; no edge claim. ARGS="--with-ta" matches the live ta_rank tilt.
cmc_pnl:
	. .venv/bin/activate && CMC_INTEL_ENABLED=true PYTHONPATH=src python scripts/cmc_pnl_curve.py $(ARGS)

# Generic Gate-A validator for ANY registered strategy (the capability arms). Prints the
# rolling-7d distribution + survival verdict vs the 25% DD ceiling. ARGS="--strategy dual_momentum
# --save-verdict" persists the backtest-survival verdict; "--list" shows all registered arms.
validate_strategy:
	. .venv/bin/activate && PYTHONPATH=src python scripts/validate_strategy.py $(ARGS)

# Forward-promotion check (Part 7): read the SIM journal, evaluate each arm's FORWARD
# track (worst-7d DD < 25% AND >= 7 t/wk AND median weekly return >= 0). ARGS="--save"
# persists the forward verdict for the dashboard selector. Most arms read "insufficient
# forward data" until run forward in SIM for ~2 weeks.
forward_promote:
	. .venv/bin/activate && PYTHONPATH=src python scripts/forward_promote.py $(ARGS)

# ONE-SHOT validation campaign: wire EVERY registered arm through validate (backtest
# survival) + forward-promote + save, then regenerate the guardian status matrix in
# docs/strategy_campaign.md and the comparison report in data/reports/strategy_campaign.md.
# Risk-first ranking; SIM-only/read-only (never touches the live strategy). The path to
# picking the contest arm — see docs/strategy_campaign.md.
#   make campaign                                  # full run, persist verdicts + docs
#   make campaign ARGS="--no-save"                 # dry run (print only)
#   make campaign ARGS="--forward-min-days 14"     # rigorous forward window
campaign:
	. .venv/bin/activate && PYTHONPATH=src:. python scripts/strategy_campaign.py $(ARGS)

# Survival-verdict STABILITY harness: grade each arm robust/fragile/unstable across disjoint
# data-window segments + friction levels + per-regime + a 60/40 holdout, and write
# data/reports/strategy_stability.md. Answers "which arm's PASS can I trust?" (the campaign's
# single worst-week DD is noisy). SIM-only/read-only — persists no verdicts. See docs/strategy_campaign.md.
#   make stability                          # all arms
#   make stability ARGS="--arm breakout"    # one arm
#   make stability ARGS="--no-save"         # print only
stability:
	. .venv/bin/activate && PYTHONPATH=src:. python scripts/strategy_stability.py $(ARGS)

# Parameter-sensitivity sweep: for each tunable arm (breakout, mean_reversion, momentum_fast),
# grid-search its key params, re-grade every config through the stability harness, and report the
# most-ROBUST config vs the arm's default (data/reports/strategy_sweep.md). Ranked stability-first
# (not best-DD) with the walk-forward overfit delta shown. READ-ONLY recommender — changes no arm.
#   make sweep_arms                          # all tunable arms
#   make sweep_arms ARGS="--arm breakout"    # one arm
#   make sweep_arms ARGS="--no-save"         # print only
sweep_arms:
	. .venv/bin/activate && PYTHONPATH=src:. python scripts/strategy_sweep.py $(ARGS)

# ISOLATED per-arm forward paper track. Runs run_allocator --mode sim for ARM into its OWN data
# tree (data/forward/$(ARM)/) via ALLOCATOR_DATA_DIR, so it NEVER clobbers the production SIM
# journal the dashboard reads. STRATEGY_NAME=$(ARM) forces the arm. Cron this every 12h to accrue
# a forward verdict over ~1-2 weeks (the verdict matures on calendar span, not tick count).
#   make forward_track ARM=dual_momentum               # one isolated sim tick
#   make forward_track ARM=dual_momentum ARGS="--reset" # wipe the isolated ledger
#   make forward_track_report ARM=dual_momentum        # read the isolated forward verdict
ARM ?= dual_momentum
forward_track:
	@mkdir -p data/forward/$(ARM)/journal
	. .venv/bin/activate && ALLOCATOR_DATA_DIR=data/forward/$(ARM) STRATEGY_NAME=$(ARM) \
		CMC_WS_DIR=$(CURDIR)/data/cache/cmc_ws \
		PYTHONPATH=src python scripts/run_allocator.py --mode sim --dd-cap 0.10 $(ARGS)

forward_track_report:
	. .venv/bin/activate && ALLOCATOR_DATA_DIR=data/forward/$(ARM) \
		PYTHONPATH=src python scripts/forward_promote.py --strategy $(ARM) $(ARGS)

# ISOLATED forward track for the CMC-NATIVE arm (momentum_cmc) under the ZERO-CEX firewall + the
# FULL CMC STACK (MCP technicals, Skills market-overview, derivatives + macro brakes) at the DQ-safe
# deploy band. Same isolated-tree contract as forward_track, but with the contest env baked in so the
# forward verdict reflects the EXACT config the live arm runs. Cron every 12h alongside cmc_stream.py:
#   40 5,17 * * *  cd <repo> && make forward_track_cmc >> data/logs/forward_cmc.log 2>&1
forward_track_cmc:
	@mkdir -p data/forward/momentum_cmc/journal
	. .venv/bin/activate && ALLOCATOR_DATA_DIR=data/forward/momentum_cmc STRATEGY_NAME=momentum_cmc \
		CMC_WS_DIR=$(CURDIR)/data/cache/cmc_ws \
		CMC_ONLY=true CMC_INTEL_ENABLED=true CMC_MCP_ENABLED=true \
		ALLOC_TA_ENABLED=true ALLOC_TA_W_RANK=1.0 ALLOC_TA_W_CAP=1.0 \
		CMC_SKILL_REGIME=true CMC_DERIV_BRAKE=true CMC_MACRO_GUARD=true \
		PYTHONPATH=src python scripts/run_allocator.py --mode sim --dd-cap 0.10 $(ARGS)

forward_track_cmc_report:
	. .venv/bin/activate && ALLOCATOR_DATA_DIR=data/forward/momentum_cmc \
		PYTHONPATH=src python scripts/forward_promote.py --strategy momentum_cmc $(ARGS)

# Forward track for the CHALLENGER mean_reversion arm under the SAME CMC firewall + full stack as
# momentum_cmc (above), so the two are forward-scored apples-to-apples on identical CMC inputs.
# mean_reversion has an ADVERSE PRIOR (reversal flips to momentum on majors) — forward-test with
# skepticism; never promote over momentum_cmc without a clear, forward-eligible win. Cron every 12h:
#   45 5,17 * * *  cd <repo> && make forward_track_meanrev >> data/logs/forward_meanrev.log 2>&1
forward_track_meanrev:
	@mkdir -p data/forward/mean_reversion_cmc/journal
	. .venv/bin/activate && ALLOCATOR_DATA_DIR=data/forward/mean_reversion_cmc STRATEGY_NAME=mean_reversion \
		CMC_WS_DIR=$(CURDIR)/data/cache/cmc_ws \
		CMC_ONLY=true CMC_INTEL_ENABLED=true CMC_MCP_ENABLED=true \
		ALLOC_TA_ENABLED=true ALLOC_TA_W_RANK=1.0 ALLOC_TA_W_CAP=1.0 \
		CMC_SKILL_REGIME=true CMC_DERIV_BRAKE=true CMC_MACRO_GUARD=true \
		PYTHONPATH=src python scripts/run_allocator.py --mode sim --dd-cap 0.10 $(ARGS)

forward_track_meanrev_report:
	. .venv/bin/activate && ALLOCATOR_DATA_DIR=data/forward/mean_reversion_cmc \
		PYTHONPATH=src python scripts/forward_promote.py --strategy mean_reversion $(ARGS)

# Tick ALL the challenger forward tracks in one shot (each in its own isolated tree). Cron this
# every 12h to accrue forward evidence over the contest week:
#   40 5,17 * * *  cd <repo> && make forward_track_all >> data/logs/forward_tracks.log 2>&1
# All 8 challengers (the incumbent momentum_adaptive owns the production journal; base momentum is
# excluded). Most sit cash-vacuous in a risk-off regime — `make readiness` now labels those
# "⏳ accruing (cash — deploy_cap≈0)" so a flat track is never mistaken for real forward evidence.
FORWARD_ARMS ?= dual_momentum breakout momentum_voltarget mean_reversion rotation momentum_fast grid momentum_mafilter
forward_track_all:
	@for arm in $(FORWARD_ARMS); do echo "--- forward_track $$arm ---"; $(MAKE) --no-print-directory forward_track ARM=$$arm; done

# Contest-readiness rollup: fuse stability + survival + forward into ONE ready/not verdict per arm
# (data/reports/contest_readiness.md). READ-ONLY; never auto-promotes. See docs/strategy_campaign.md.
#   make readiness                          # all arms
#   make readiness ARGS="--no-save"         # print only
#   make readiness ARGS="--forward-min-days 14"
readiness:
	. .venv/bin/activate && PYTHONPATH=src:. python scripts/contest_readiness.py $(ARGS)

# Playbook ↔ implementation status: wire each Top-10 family in docs/strategy_playbook.md to its
# registered + validated arm and splice the §11 status matrix (survival GATE + stability +
# forward + the PnL/win-rate SCOREBOARD). READ-ONLY; rewrites only between the PLAYBOOK markers.
# Run `make campaign` + `make stability` first to populate. Parity is enforced by
# tests/test_playbook_parity.py. See the VALIDATION UPDATE banner in docs/strategy_playbook.md.
#   make playbook                     # splice the §11 matrix
#   make playbook ARGS="--no-save"    # print only
playbook:
	. .venv/bin/activate && PYTHONPATH=src:. python scripts/playbook_status.py $(ARGS)

# Sim-test EVERY registered strategy: tick all challenger arms fresh (forward_track_all, each in its
# isolated SIM tree) then VALIDATE each arm's journal + state — schema, NAV, weights ≤ 1, universe-only
# tokens, n_swaps↔tx, ledger round-trip — and surface the distinct tokens each trades. The incumbent
# falls back to the production SIM journal. Writes data/reports/sim_test_all.md. READ-ONLY/SIM.
#   make sim_test_all                  # tick all arms fresh, then validate
#   make sim_test_all ARGS="--no-save" # validate the existing tracks only, print
sim_test_all:
	$(MAKE) --no-print-directory forward_track_all
	. .venv/bin/activate && PYTHONPATH=src:. python scripts/sim_test_all.py $(ARGS)

# CMC measure-first diagnostic: report which CMC data/skills are live vs degraded vs flag-off in the
# CURRENT config (F&G, regime intel, TA cap+rank, market-overview skill, x402, MCP) + what actually
# flowed in recent journal rows. Writes data/reports/cmc_status.md. READ-ONLY (a few probe credits).
# To MEASURE the backtestable levers' PnL effect: `make ab_regime`.
cmc_check:
	. .venv/bin/activate && PYTHONPATH=src:. python scripts/cmc_check.py $(ARGS)

# MCP verification: LIVE-probe the CMC MCP (tools/list + a sample tools/call) and map each SKILL to
# its responding tool → "MCP LIVE — N/12 tools, M skills paired" + data/reports/mcp_status.md.
# Proves the wiring the dashboard/journal already show. READ-ONLY (a few CMC credits). See
# docs/mcp_wiring.md. (probe_agent_hub = the deeper one-shot probe: schemas + sample TA + x402.)
mcp_check:
	. .venv/bin/activate && PYTHONPATH=src:. python scripts/mcp_check.py $(ARGS)

probe_agent_hub:
	. .venv/bin/activate && PYTHONPATH=src python scripts/probe_agent_hub.py $(ARGS)

# ONE-COMMAND PROJECT VALIDATION (the step-by-step runbook in docs/strategy_validation_runbook.md):
# offline invariants + parity (pytest) -> locked-default unchanged (validate_allocator) -> per-arm
# survival + PnL/win-rate scoreboard (campaign) -> trustworthiness grades (stability) -> fused
# readiness rollup -> playbook §11 status. ONLINE (campaign/stability fetch the universe) — minutes,
# not a CI step. The offline always-green subset is `make test`.
validate_all:
	. .venv/bin/activate && PYTHONPATH=src python -m pytest -q tests/test_performance.py tests/test_playbook_parity.py tests/test_strategy_campaign.py tests/test_strategy_registry.py tests/test_strategy_aliases.py
	$(MAKE) --no-print-directory validate_allocator
	$(MAKE) --no-print-directory campaign
	$(MAKE) --no-print-directory stability
	$(MAKE) --no-print-directory readiness
	$(MAKE) --no-print-directory playbook

# CMC PnL A/B: prove + tune whether the enhanced regime / tilt / ranking improve SIM PnL
# (writes data/journal/cmc_pnl_ab*.json + docs/cmc_pnl_ab.md). Add ARGS="--sweep".
ab_regime:
	. .venv/bin/activate && PYTHONPATH=src python scripts/ab_regime.py $(ARGS)

# PnL-campaign sweep: grid-search the allocator levers under the campaign rules
# (10% DD halt + profit-lock ratchet) and rank by P(9-day outcome >= +5%).
# Writes data/journal/campaign_sweep.json. ARGS="--quick" for a smoke run.
sweep_campaign:
	. .venv/bin/activate && PYTHONPATH=src python scripts/sweep_campaign.py $(ARGS)

# Live runtime: one momentum-allocator rebalance tick (sim by default).
#   make run_allocator                       # one sim tick (paper ledger persists)
#   make run_allocator ARGS="--reset"        # wipe paper ledger + HWM
#   make run_allocator ARGS="--loop --ticks 3 --interval-min 1"
#   make run_allocator ARGS="--mode live"    # real BSC swaps (needs [bsc] + ENABLE_LIVE_TRADING)
run_allocator:
	. .venv/bin/activate && PYTHONPATH=src python scripts/run_allocator.py $(ARGS)

# Forward paper track record: summarise the live rebalance journal
# (NAV, drawdown, trades, regime/cap/F&G evolution) written by run_allocator. This
# is the FORWARD out-of-sample evidence — run after a few days of scheduled ticks.
forward_report:
	. .venv/bin/activate && PYTHONPATH=src python scripts/report_forward.py $(ARGS)

# One-shot on-chain agent registration (ERC-8004 identity + CompetitionRegistry).
# Needs `pip install -e ".[bsc]"` + AGENT_PRIVATE_KEY + BSC_RPC_URL in .env.
register_agent:
	. .venv/bin/activate && PYTHONPATH=src python scripts/register_agent.py $(ARGS)

# Trigger + verify the NodeReal/MegaFuel gasless link (read-only; no mint, no spend).
# Proves the keyed endpoint reaches YOUR dashboard on testnet + mainnet and reports
# whether the sponsor policy is live.  make verify_nodereal ARGS="--network testnet"
verify_nodereal:
	. .venv/bin/activate && PYTHONPATH=src python scripts/verify_nodereal.py $(ARGS)

# Re-mint a FRESH ERC-8004 identity owned by the LOCAL keystore (agentId 1313 was
# minted by a key no longer on this machine — heartbeats can't sign). DRY-RUN by
# default; refuses to mint until a gas path is ready (MegaFuel sponsor policy OR
# AGENT_USE_PAYMASTER=false + funded wallet).  make remint_identity ARGS="--mint"
remint_identity:
	. .venv/bin/activate && PYTHONPATH=src python scripts/remint_identity.py $(ARGS)

# Refresh the dashboard's static fallback (web/public/snapshot.json) from the live
# journal — incl. the three-pillar status block. Run before committing/deploying the SPA.
snapshot:
	PYTHONPATH=src .venv/bin/python scripts/export_snapshot.py

# ONE-COMMAND dashboard redeploy (post-faucet / after a real ERC-8183 job settles).
# Regenerates the snapshot (incl. pillars.commerce), reseeds infra/seed, builds, and deploys the
# PREBUILT output to the canonical Vercel project (bnb-mission-control-two.vercel.app). The plain
# `vercel --prod` build fails on the project's framework preset, so we ship the Build Output API.
#   ERC8183_ENABLED=true make deploy_dashboard
deploy_dashboard:
	bash scripts/deploy_dashboard.sh

# Regenerate the full-system architecture diagram (docs/architecture.{excalidraw,svg,png}).
# Pure-stdlib for the .excalidraw + .svg; the .png is emitted only if cairosvg is installed (skipped
# gracefully otherwise). The x402 receipt count/total and ERC-8183 job ids are derived LIVE from the
# journals at build time and the script self-asserts every layer token is present — so the diagram
# can't drift from the implementation. See ARCHITECTURE.md.
architecture:
	. .venv/bin/activate && python scripts/gen_architecture.py

# Headless QA of the DEPLOYED dashboard — renders the SPA via system Chrome and asserts the integrated
# panels (CMC Agent-Hub rotation, Agent Commerce, Identity heartbeat) + core render with live data, and
# the no-"Binance" display gate is green. Writes data/reports/dashboard_verification.{md,png}. Warm the
# Render API first (it cold-starts) so the SPA fetch lands inside the budget.
#   make verify_dashboard            # the deployed site
#   DASH_URL=http://localhost:5173 make verify_dashboard
verify_dashboard:
	@curl -s -m 30 https://bnb-mission-control-api.onrender.com/api/health -o /dev/null || true
	node scripts/verify_dashboard.mjs

# ARM CHECK — one read-only command that runs EVERY no-funds go-live check (TWAK creds +
# binary + router price, ENABLE_LIVE_TRADING, strategy, ERC-8004 heartbeat gas, ERC-8183
# buyer, x402 pay wallet) and prints a single readiness table. Non-zero only on a real
# blocker (✗); a money-only gap (⛽) is reported but does NOT fail. The pre-flip gate.
#   make arm_check
arm_check:
	PYTHONPATH=src .venv/bin/python scripts/arm_check.py

# ERC-8004 heartbeat readiness + on-chain read-back (read-only; no mint/swap/spend). Prints the
# funding-path status (gasless sponsorable, or direct-gas identity-wallet BNB) and the latest
# on-chain heartbeat — the actionable antidote to the old silent heartbeat failure.
#   make heartbeat_check
heartbeat_check:
	PYTHONPATH=src .venv/bin/python scripts/heartbeat_check.py

# ERC-8183 commerce — create + serve a REAL job so the dashboard ledger fills (operator-local).
# Network via ERC8183_NETWORK (bsc-testnet | bsc-mainnet). Needs the provider keystore
# (AGENT_WALLET_PASSWORD) + a distinct buyer keystore (CLIENT_WALLET_PASSWORD [+ CLIENT_WALLET_DIR]).
#   make commerce_wallet            # show the buyer address + payment token to FUND (read-only)
#   make commerce_job               # run create -> fund -> serve -> settle (ARGS="--query '...'")
commerce_wallet:
	PYTHONPATH=src .venv/bin/python scripts/erc8183_create_job.py --show-wallet $(ARGS)

commerce_job:
	PYTHONPATH=src .venv/bin/python scripts/erc8183_create_job.py $(ARGS)

# Autonomous PROVIDER loop — SELL the Market Regime Report to EXTERNAL buyers' funded jobs. Needs only
# the provider keystore (AGENT_WALLET_PASSWORD from .env). Ctrl-C to stop.
#   make commerce_serve             # poll loop  (ARGS="--once" | "--interval 15")
#   make commerce_serve_check       # wiring probe: provider addr + pending funded jobs (no signing)
commerce_serve:
	ERC8183_ENABLED=true ERC8183_NETWORK=bsc-mainnet PYTHONPATH=src .venv/bin/python scripts/erc8183_serve.py $(ARGS)

commerce_serve_check:
	ERC8183_ENABLED=true ERC8183_NETWORK=bsc-mainnet PYTHONPATH=src .venv/bin/python scripts/erc8183_serve.py --check

# SUPERVISED provider daemon — always-on "open for business": each cycle runs ONE fresh
# `erc8183_serve.py --once` (auto-heals a hung watcher), polls FUNDED jobs (0 gas), submits on a
# real funded job. Needs only the provider keystore (AGENT_WALLET_PASSWORD from .env). Run detached:
#   make commerce_serve_check                                       # preflight wiring probe
#   nohup bash scripts/commerce_serve.sh >> data/logs/commerce_serve.log 2>&1 &
#   tail -f data/logs/commerce_serve.log    ·   pkill -f scripts/commerce_serve.sh   # stop
commerce_serve_daemon:
	bash scripts/commerce_serve.sh

# FINALIZE served-but-unsettled jobs once their ~7-day OPTIMISTIC dispute window has closed (the
# buyer signs settle(); "deferred" = window still open = expected, not an error). Buyer keys read
# from .env / ~/.bnbagent and stay LOCAL — never the cloud.
#   make commerce_settle            # settle ALL pending  (ARGS="--job-ids 25741 26506")
commerce_settle:
	ERC8183_ENABLED=true ERC8183_NETWORK=bsc-mainnet \
	  CLIENT_WALLET_PASSWORD="$$(cat $$HOME/.bnbagent/buyer-main.pass)" \
	  CLIENT_WALLET_DIR="$$HOME/.bnbagent/buyer-main" \
	  PYTHONPATH=src .venv/bin/python scripts/erc8183_create_job.py --settle-pending $(ARGS)

# ONE-COMMAND dashboard data refresh. The live dashboard reads TWO sources off the
# allocator journal: the Render API bakes infra/seed/* into its image (the SPA's PRIMARY
# source), and web/public/snapshot.json is the Vercel offline FALLBACK. After a new
# forward tick, both must be refreshed or the UI shows stale PnL. This regenerates the
# snapshot AND reseeds infra/seed/, then prints the exact commit/deploy commands — the
# outward-facing git push (auto-redeploys Render) + `vercel --prod` stay operator-run.
#   make refresh_dashboard          # prep both sources, then follow the printed steps
refresh_dashboard: snapshot
	@test -f data/journal/allocator_journal.jsonl || { echo "ERROR: no data/journal/allocator_journal.jsonl — run a tick first"; exit 1; }
	cp data/journal/allocator_journal.jsonl data/journal/allocator_state.json infra/seed/
	@PYTHONPATH=src .venv/bin/python -c "import json; r=[json.loads(l) for l in open('infra/seed/allocator_journal.jsonl') if l.strip()]; b=[x for x in r if x.get('event')=='REBALANCE']; print(f'  reseeded infra/seed + snapshot -> {len(b)} ticks, NAV {b[-1][\"nav_after\"]} ({b[-1][\"ts\"]})')"
	@for f in strategy_gates.json strategy_stability.json strategy_evaluation.json; do \
	  if [ -f data/reports/$$f ]; then cp data/reports/$$f infra/seed/ && echo "  reseeded strategy report -> infra/seed/$$f (Strategy Lab)"; \
	  else echo "  (skip $$f — run 'make campaign' + 'make stability' to populate the Strategy Lab)"; fi; \
	done
	@if [ -f data/journal/cmc_mcp_usage.json ]; then cp data/journal/cmc_mcp_usage.json infra/seed/ && echo "  reseeded CMC MCP telemetry -> infra/seed/cmc_mcp_usage.json (Agent Hub tools count)"; \
	else echo "  (skip cmc_mcp_usage.json — no CMC MCP telemetry journaled yet)"; fi
	@echo ""
	@echo "  next (operator-run — outward-facing):"
	@echo "    git add web/public/snapshot.json infra/seed/allocator_journal.jsonl infra/seed/allocator_state.json infra/seed/strategy_gates.json infra/seed/strategy_stability.json infra/seed/strategy_evaluation.json infra/seed/cmc_mcp_usage.json"
	@echo "    git commit -m 'chore(dashboard): refresh PnL data' && git push    # Render auto-redeploys"
	@echo "    vercel --prod --yes                                               # deploy the Vercel SPA"
	@echo "  verify: curl -s https://bnb-mission-control-api.onrender.com/api/nav | python -c 'import sys,json;print(json.load(sys.stdin)[\"current_nav\"])'"

# Phase 9.A: per-pair WFO driver. Writes data/wfo/per_pair_<UTC-date>.json
# with the winning (sl_frac, tp_frac, poi_tol) per pair + classify verdict.
# Override:  make wfo_per_pair ARGS="--bars 10000 --grid rr2plus"
ARGS ?=
wfo_per_pair:
	. .venv/bin/activate && python scripts/wfo_per_pair.py $(ARGS)

# Phase 9.G: per-pair Phase 9 acceptance gate. Exit 0 when every configured
# pair has ≥ 1 broker-truth close; exit 1 when any pending. Run daily.
smoke_gate:
	. .venv/bin/activate && python scripts/diagnose_live_pnl.py --smoke-gate

# Phase 9.F: round-trip every pair on Binance testnet (market entry +
# reduceOnly flatten). Refuses unless BINANCE_TESTNET=true. Writes
# data/smoke_pairs_<UTC-date>.json with per-pair status + latency.
#   make smoke_pairs                # all configured pairs
#   make smoke_pairs ARGS="--pair BTC/USDT:USDT"
#   make smoke_pairs ARGS="--dry-run"
smoke_pairs:
	. .venv/bin/activate && python scripts/smoke_test_pairs.py $(ARGS)

# Phase 9.E: per-pair boot readiness check. Prints leverage / margin /
# ticker / min_notional / sized_qty per configured pair. Used pre-restart
# to verify the live broker won't refuse to boot under STRICT_PAIR_INIT=true.
pair_readiness:
	. .venv/bin/activate && python -c "from ictbot.exec.binance_live import BinanceLiveBroker; \
		from ictbot.settings import settings; \
		b = BinanceLiveBroker(allowed_pairs=set(settings.pairs), \
		testnet=settings.binance_testnet, \
		api_key=settings.binance_api_key, api_secret=settings.binance_api_secret); \
		[print(f'{p:<22} {s}') for p, s in b.verify_all_pairs_ready().items()]"

# Phase 13.C: one-shot ops snapshot. Wallet + open positions + smoke gate +
# heartbeat + last 5 broker-truth closes. Read-only; no orders placed.
#   make status                 # pretty-printed
#   make status ARGS="--json"   # machine-readable
status:
	. .venv/bin/activate && python scripts/status.py $(ARGS)

# Phase 14.D: statistical edge check. Per-pair t-stat vs 0 + vs WFO TEST
# expectancy. Exit 0 if any pair has confirmed edge (n≥30, |t|>2,
# mean>0); 1 if pending more data; 2 if no broker-truth closes yet.
#   make edge_check                            # pretty-printed
#   make edge_check ARGS="--json"              # machine-readable
#   make edge_check ARGS="--min-n 20"          # lower the bar
#   make edge_check ARGS="--wfo path/to.json"  # custom WFO baseline
edge_check:
	. .venv/bin/activate && python scripts/edge_check.py $(ARGS)

# Phase 16.C: session-bucketed daily trade report. Writes markdown to
# data/reports/session_<UTC-date>.md with IN_SESSION (London+NY) vs
# OFF_SESSION (Tokyo+off-hours) per-bucket stats, Welch's t comparison,
# per-pair breakdown, trade-by-trade log, and cap-rejection breakdown.
#   make session_report                              # today UTC
#   make session_report ARGS="--date 2026-06-07"     # specific UTC day
#   make session_report ARGS="--no-write"            # stdout only
#   make session_report ARGS="--out /tmp/x.md"       # custom path
session_report:
	. .venv/bin/activate && python scripts/session_report.py $(ARGS)

# Write the latest backtest's equity curve for the dashboard
bt_curve:
	. .venv/bin/activate && python -m ictbot.engine.bt_curve "$(PAIR)" --bars $(BARS)

# Position sizing: make size BALANCE=1000 RISK=1 ENTRY=77000 SL=76600
BALANCE ?= 1000
RISK ?= 1
size:
	. .venv/bin/activate && python -m ictbot.engine.sizing --balance $(BALANCE) --risk $(RISK) --entry $(ENTRY) --sl $(SL)

# Kelly + risk-of-ruin reading.  make kelly WINRATE=50 RR=3
WINRATE ?= 50
RR ?= 3
kelly:
	. .venv/bin/activate && python -m ictbot.engine.sizing --balance $(BALANCE) --kelly --win-rate $(WINRATE) --rr $(RR)

ror:
	. .venv/bin/activate && python -m ictbot.engine.sizing --balance $(BALANCE) --ror --risk $(RISK) --win-rate $(WINRATE) --rr $(RR)

journal:
	. .venv/bin/activate && python -m ictbot.cli.journal_cmd

# Compare sma / swing / slope bias engines side-by-side on one pair
bias_compare:
	. .venv/bin/activate && python -m ictbot.engine.compare "$(PAIR)" --bars $(BARS)

# Cross-pair bias scoreboard — runs compare on every PAIR
bias_scoreboard:
	. .venv/bin/activate && python -m ictbot.engine.compare --all --bars $(BARS)

clean:
	rm -rf .venv .pytest_cache __pycache__ */__pycache__ src/**/__pycache__
	rm -f data/journal/last_signal.json data/journal/signals.json
	rm -f data/runs/backtest_curve.json data/logs/scanner.log
