"""
Global config for ICT AI BOT PRO MAX, backed by pydantic-settings.

The single `Settings` class is the source of truth. For backwards
compatibility with code that does `from ictbot.settings import PAIRS,
HTF_TIMEFRAME, …`, every field is re-exported at module level after
hydration. New code is encouraged to read `settings.<field>` directly so
typing flows through.

Env vars override defaults. `.env` at the project root is loaded
automatically. Unknown env keys are ignored (we keep legacy keys like
`SYMBOLS=…` around even though no code reads them any more).
"""

import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# settings.py lives at src/ictbot/settings.py → parents[2] = repo root
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_data_dir(override: str | None, project_root: Path) -> Path:
    """Base data dir. ``ALLOCATOR_DATA_DIR`` redirects ALL data (journal/state/reports) to an
    isolated tree — used to run a per-arm forward paper track (e.g. ``data/forward/dual_momentum``)
    WITHOUT clobbering the production SIM journal the dashboard reads. Unset = the in-repo ``data/``
    (unchanged default)."""
    return Path(override) if override else project_root / "data"


DATA_DIR = _resolve_data_dir(os.environ.get("ALLOCATOR_DATA_DIR"), PROJECT_ROOT)
JOURNAL_DIR = DATA_DIR / "journal"
RUNS_DIR = DATA_DIR / "runs"
LOGS_DIR = DATA_DIR / "logs"
CACHE_DIR = DATA_DIR / "cache"
for _d in (JOURNAL_DIR, RUNS_DIR, LOGS_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Shared CMC WebSocket snapshot cache (CEX quotes + onchain@* harvest). The on-chain signals are
# LIVE SHARED data — ONE streamer feeds every consumer — so unlike the per-run data tree this must
# NOT be redirected by ALLOCATOR_DATA_DIR. Default = CACHE_DIR/cmc_ws (so tests/smoke that isolate
# ALLOCATOR_DATA_DIR stay hermetic), but `CMC_WS_DIR` points every isolated forward/sim track at the
# SAME production snapshots the live streamer writes — without it the overlays read empty + no-op.
CMC_WS_DIR = (
    Path(os.environ["CMC_WS_DIR"]).expanduser()
    if os.environ.get("CMC_WS_DIR")
    else CACHE_DIR / "cmc_ws"
)
CMC_WS_DIR.mkdir(parents=True, exist_ok=True)


# Default pairs scanned by scanner.py.
#
# Phase 11 (2026-06-06): dropped PAXG/USDT:USDT after the Phase 9.A
# WFO returned `no edge` (TRAIN expectancy -0.85R, TEST 0/4 on the
# winning grid cells). Operational quirks (off-hours liquidity, -4047
# margin lock, 3 of 4 PAXG broker-truth closes were MANUAL settlements
# rather than natural TP/SL fills) confirmed the verdict.
# See data/wfo/per_pair_2026-06-06.json (legacy Phase 11).
_DEFAULT_PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
]


class Settings(BaseSettings):
    """Single config object. Override any field via env / .env."""

    # Test isolation knob: subprocess tests in test_settings_boot_guards
    # set ICTBOT_SKIP_DOTENV=1 so pydantic-settings doesn't read the
    # operator's real .env. Without this, an operator's local override
    # (e.g. MAX_OPEN_POSITIONS=9999) leaks into "default value" tests
    # because env_file is an absolute path that ignores cwd.
    model_config = SettingsConfigDict(
        env_file=(
            None
            if __import__("os").environ.get("ICTBOT_SKIP_DOTENV") == "1"
            else PROJECT_ROOT / ".env"
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Telegram --------------------------------------------------------
    telegram_token: str = Field(default="", alias="TELEGRAM_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # ---- Exchange --------------------------------------------------------
    # `delta` = delta.exchange perpetuals (mainnet target), `binance` =
    # Binance USDT-M Futures (testnet, current testing venue). The factory
    # in data/factory.py + exec/factory.py read this to pick adapter +
    # broker. Both adapters use the same ccxt symbol format
    # (e.g. BTC/USDT:USDT) so PAIRS doesn't need to change on swap.
    exchange: Literal["delta", "binance"] = "delta"
    delta_api_key: str = Field(default="", alias="DELTA_API_KEY")
    delta_api_secret: str = Field(default="", alias="DELTA_API_SECRET")
    # Binance USDT-M Futures. Keys come from
    # https://testnet.binancefuture.com (no KYC required, USDT faucet on site).
    # When BINANCE_TESTNET=true, the broker rewrites URLs to
    # testnet.binancefuture.com via ccxt.set_sandbox_mode.
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")
    binance_testnet: bool = Field(default=False, alias="BINANCE_TESTNET")

    # ---- Pairs -----------------------------------------------------------
    pairs: list[str] = Field(default_factory=lambda: list(_DEFAULT_PAIRS))
    ui_pairs: list[str] = Field(default_factory=lambda: list(_DEFAULT_PAIRS))

    # ---- Timeframes ------------------------------------------------------
    htf_timeframe: str = "4h"
    bias_timeframe: str = "15m"
    poi_timeframe: str = "3m"
    entry_timeframe: str = "1m"

    # ---- Strategy knobs --------------------------------------------------
    # Phase A of the canonical-flow alignment: the spec wants follow
    # bias, swing-MSS-derived bias, and OB POIs. Defaults flipped to
    # match. The `canonical_flow` kill-switch below can revert ALL of
    # these to legacy values at once via env var, no code change needed.
    #
    # B3 (ROADMAP §B3): widened from 0.0015 → 0.005. Every config that
    # held out-of-sample in findings §15 used 0.005 or larger; the old
    # 0.0015 default was below the signal-count floor for high-RR setups.
    poi_tap_tolerance: float = 0.005
    poi_engine: Literal["min_max", "order_block"] = "order_block"
    bias_engine: Literal["sma", "swing", "slope"] = "swing"
    strategy_mode: Literal["follow", "fade"] = "follow"

    # ---- Canonical-flow kill-switch -------------------------------------
    # "on"  (default) — every flag set above + sl_anchor / etc honour the
    #                   canonical ICT flow from the spec.
    # "off"           — forces poi_engine, bias_engine, strategy_mode,
    #                   sl_anchor back to legacy values regardless of any
    #                   per-field override. The single env var
    #                   CANONICAL_FLOW=off rolls everything back without
    #                   touching code or other env vars.
    canonical_flow: Literal["on", "off"] = "on"

    # ---- News-blackout gate (Forex Factory) ------------------------------
    # 0 disables the gate (default). When > 0, the strategy blocks entries
    # within ±N minutes of any matching high-impact macro event.
    news_blackout_minutes: float = 0.0
    # Currencies whose events trip the gate. USD covers the bulk of crypto-
    # relevant macro (CPI, FOMC, NFP). Add others as needed.
    news_blackout_countries: list[str] = Field(default_factory=lambda: ["USD"])
    # FF impact tiers that count. High only by default.
    news_blackout_impacts: list[str] = Field(default_factory=lambda: ["High"])
    # Standalone news alerter (independent of the gate). When > 0, the
    # scanner pings Telegram once per event entering this many minutes ahead.
    # Dedup persists in data/journal/news_alerts.json across restarts.
    news_alert_window_min: float = 0.0

    # ---- Backtest realism -----------------------------------------------
    fee_per_side: float = 0.0005
    slippage_per_side: float = 0.0002
    trail_breakeven_r: float | None = None

    # ---- Live strategy: momentum allocator + TWAK spot execution --------
    # The live agent is a long-only spot PORTFOLIO REBALANCER (not the ICT
    # signal path). It runs via scripts/run_allocator.py and is fully decoupled
    # from `exchange` (which still selects the delta/binance CEX brokers). See
    # src/ictbot/strategy/momentum_allocator.py for the validated rationale.
    cmc_api_key: str = Field(default="", alias="CMC_API_KEY")
    # 'sim' = paper execution against the price feed (safe default, no key).
    # 'live' = sign real BSC swaps via the `twak` CLI (needs creds + a wallet +
    #          ENABLE_LIVE_TRADING=true).
    twak_mode: Literal["sim", "live"] = "sim"
    # TWAK API credentials. The official `twak` CLI reads TWAK_ACCESS_ID /
    # TWAK_HMAC_SECRET from the env; we also accept the TW_ prefix (what the user
    # typed). CliTwakClient injects these into the twak subprocess under the
    # canonical names. TWAK_WALLET_PASSWORD unlocks the agent wallet to EXECUTE
    # swaps — read-only calls (price) don't need it.
    twak_access_id: str = Field(
        default="", validation_alias=AliasChoices("TWAK_ACCESS_ID", "TW_ACCESS_ID")
    )
    twak_hmac_secret: str = Field(
        default="", validation_alias=AliasChoices("TWAK_HMAC_SECRET", "TW_HMAC_SECRET")
    )
    twak_wallet_password: str = Field(
        default="", validation_alias=AliasChoices("TWAK_WALLET_PASSWORD", "TW_WALLET_PASSWORD")
    )
    twak_chain: str = "bsc"
    # The `twak` CLI binary. It is an npm global (`@trustwallet/cli`) and is often NOT
    # on a cron/daemon's minimal PATH (e.g. installed under nvm). Set TWAK_BINARY to the
    # ABSOLUTE path so unattended runs resolve it; CliTwakClient also prepends the
    # binary's own directory to the subprocess PATH so the co-located `node` (twak's
    # `#!/usr/bin/env node` shebang) is found even with an empty PATH.
    twak_binary: str = Field(default="twak", alias="TWAK_BINARY")
    # TwakSpotBroker dust filters: skip rebalance legs smaller than `min_rebal_frac` of NAV
    # OR `min_swap_usd` absolute notional (avoids churn + fee bleed on tiny moves). Tunable
    # because a SMALL contest bankroll makes 1/k-weight positions sub-$1 — at the $1 default
    # the agent would skip EVERY swap and never trade. Lower both for a small book.
    alloc_min_swap_usd: float = Field(default=1.0, alias="ALLOC_MIN_SWAP_USD")
    alloc_min_rebal_frac: float = Field(default=0.02, alias="ALLOC_MIN_REBAL_FRAC")
    # ---- On-chain agent IDENTITY via the bnbagent SDK -------------------
    # The agent mints an ERC-8004 identity NFT (gas-free via MegaFuel on testnet).
    # twak does NOT export its wallet key, so bnbagent SELF-MANAGES its own identity
    # wallet from just a password (EVMWalletProvider creates + persists a keystore at
    # ~/.bnbagent/wallets/). Good separation: the identity key is NOT the funds key.
    # The identity profile embeds AGENT_TRADING_ADDRESS (the twak wallet) to link them.
    #   agent_wallet_password : encrypts the identity keystore (reuse the twak password).
    #   agent_private_key     : OPTIONAL — set only to unify identity with a known key.
    #   agent_trading_address : the twak wallet that actually trades (for the profile).
    agent_wallet_password: str = Field(
        default="", validation_alias=AliasChoices("AGENT_WALLET_PASSWORD", "TWAK_WALLET_PASSWORD")
    )
    agent_private_key: str = Field(default="", alias="AGENT_PRIVATE_KEY")
    agent_trading_address: str = Field(default="", alias="AGENT_TRADING_ADDRESS")
    # PUBLIC identity wallet address for DISPLAY ONLY (no key needed). The read-only
    # dashboard shows this + reads its on-chain balance; it never signs. Set this on
    # a deployed dashboard instead of AGENT_PRIVATE_KEY/AGENT_WALLET_PASSWORD so NO
    # fund-controlling secret ever leaves your machine. Falls back to deriving the
    # address from the key/password locally (for the signing paths) when unset.
    agent_identity_address: str = Field(default="", alias="AGENT_IDENTITY_ADDRESS")
    agent_endpoint: str = Field(
        default="", alias="AGENT_ENDPOINT"
    )  # https URL; default = BscScan page
    agent_name: str = Field(default="RegimeAdaptiveMomentumAgent", alias="AGENT_NAME")
    # Mainnet by default (BSC 56). The SDK preset key is "bsc-mainnet"; identity.py
    # maps "bsc" -> "bsc-mainnet" before calling resolve_network (which rejects "bsc").
    agent_network: Literal["bsc-testnet", "bsc"] = Field(default="bsc", alias="AGENT_NETWORK")
    agent_description: str = Field(
        default="Long-only spot momentum allocator: reads CMC (price, Fear&Greed) to score "
        "market regime, scales deployment into the top BSC momentum tokens, and signs "
        "+ executes spot swaps via TWAK (native gas); mints + heartbeats its ERC-8004 "
        "identity gaslessly via MegaFuel. Risk-first, DQ-safe.",
        alias="AGENT_DESCRIPTION",
    )
    # ---- NodeReal / MegaFuel gasless (identity plumbing) ----------------
    # These three match the keys already present in .env. Without nodereal_api_key
    # the SDK falls back to the PUBLIC MegaFuel endpoint (so the user's keyed sponsor
    # app sees zero requests); identity.py builds a keyed NetworkConfig from these.
    #   nodereal_api_key : routes gasless writes to open-platform-ap.nodereal.io/{key}/...
    #   bsc_rpc_https_url: optional keyed BSC RPC (overrides the public dataseed node).
    nodereal_api_key: str = Field(default="", alias="NODEREAL_API_KEY")
    bsc_rpc_https_url: str = Field(default="", alias="BSC_RPC_HTTPS_URL")
    bsc_rpc_wss_url: str = Field(default="", alias="BSC_RPC_WSS_URL")
    agent_use_paymaster: bool = Field(default=True, alias="AGENT_USE_PAYMASTER")
    # Per-tick gasless on-chain heartbeat (set_metadata via MegaFuel). Default OFF —
    # each write is a sponsored mainnet tx. Needs agent_id (captured after the mint).
    agent_heartbeat_enabled: bool = Field(default=False, alias="AGENT_HEARTBEAT_ENABLED")
    agent_id: int = Field(default=0, alias="AGENT_ID")  # ERC-8004 token id; set after first mint
    # ---- ERC-8183 agentic commerce (the agent SELLS its Market Regime Report to other agents) ----
    # The provider side of a two-sided agent economy: x402 BUYS data, ERC-8183 SELLS analysis.
    # Default OFF + bsc-testnet (the SDK's public gasless MegaFuel — free, no NodeReal key). Flip
    # ERC8183_NETWORK=bsc-mainnet for real on-chain commerce. SECURITY: enabling requires the local
    # AGENT_WALLET_PASSWORD (the same identity keystore) — never runs on the read-only deploy.
    erc8183_enabled: bool = Field(default=False, alias="ERC8183_ENABLED")
    erc8183_network: str = Field(default="bsc-testnet", alias="ERC8183_NETWORK")
    erc8183_service_price: int = Field(
        default=10000, alias="ERC8183_SERVICE_PRICE"
    )  # payment-token min units
    erc8183_storage: str = Field(default="local", alias="ERC8183_STORAGE")  # local | ipfs
    erc8183_agent_url: str = Field(
        default="", alias="ERC8183_AGENT_URL"
    )  # for file:// deliverable routing
    # BUYER keystore for the operator-local "create a job from the UI" demo: a DISTINCT agent that
    # pays our provider so the loop is genuinely agent-to-agent. Set only on a LOCAL operator run
    # (never on the read-only deploy) — its absence makes `commerce.buyer_available()` False, so the
    # dashboard's "create job" button stays disabled in the cloud. Fund this wallet from a testnet
    # faucet once (the endpoint surfaces its address on insufficient balance).
    erc8183_client_password: str = Field(default="", alias="CLIENT_WALLET_PASSWORD")
    erc8183_client_private_key: str = Field(default="", alias="CLIENT_PRIVATE_KEY")
    # Public buyer address — disambiguates which keystore to load when >1 exists in
    # ~/.bnbagent/wallets (the provider keystore lives there too). Optional when only the buyer
    # keystore is present.
    erc8183_client_address: str = Field(default="", alias="CLIENT_WALLET_ADDRESS")
    # Optional separate keystore dir for the buyer (cleanest isolation from the provider keystore;
    # avoids the "multiple wallets" ambiguity). Default: the SDK's ~/.bnbagent/wallets.
    erc8183_client_wallets_dir: str = Field(default="", alias="CLIENT_WALLET_DIR")
    # ---- CMC x402 (AI Agent Hub paid data; payment leg is USDC on BASE) ---
    # x402 pays per request ($0.01 USDC) instead of a Pro-API key. Default OFF until
    # the Base USDC payment wallet is funded; cmc.py pro-api stays the fallback.
    x402_enabled: bool = Field(default=False, alias="X402_ENABLED")
    x402_usdc_base_address: str = Field(
        default="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", alias="X402_USDC_BASE_ADDRESS"
    )  # USDC on Base (6 dp)
    x402_max_value_per_call_units: int = Field(
        default=10000, alias="X402_MAX_VALUE_PER_CALL_UNITS"
    )  # 6dp -> $0.01
    x402_session_budget_units: int = Field(
        default=1_000_000, alias="X402_SESSION_BUDGET_UNITS"
    )  # 6dp -> $1.00
    x402_base_rpc_url: str = Field(
        default="https://mainnet.base.org", alias="X402_BASE_RPC_URL"
    )  # to read USDC balance
    # ---- vlayer data-provenance attestations (Web Proofs / zkTLS) ----------
    # The vlayer/ sub-project proves an HTTPS response really came from alternative.me (Fear & Greed) and
    # records it in RegimeVerifier. This flag-gated, READ-ONLY, key-free bridge
    # (agent/provenance.py) reads that attestation so the SOLD ERC-8183 report can carry a
    # buyer-verifiable `provenance_proof`. OFF by default; reading needs no wallet/key, so it is
    # safe on the zero-secret dashboard deploy. See docs/vlayer/INTEGRATION_PLAN.md.
    vlayer_enabled: bool = Field(default=False, alias="VLAYER_ENABLED")
    vlayer_verifier_address: str = Field(default="", alias="VLAYER_VERIFIER_ADDRESS")
    vlayer_rpc_url: str = Field(
        default="https://sepolia.optimism.io", alias="VLAYER_RPC_URL"
    )
    vlayer_chain: str = Field(default="optimism-sepolia", alias="VLAYER_CHAIN")
    # ---- CMC API client: rate-limit + credit budget + market intel --------
    # The Startup commercial tier gives 300k credits/mo + 30 req/min + 28 endpoints.
    # ALL CMC calls route through src/ictbot/data/cmc_client.py (the CMC singleton),
    # which rate-limits, tracks credit spend (data/journal/cmc_usage.json), retries on
    # 429/5xx, and TTL-caches per endpoint — so heavy use never blows the caps. Soft
    # budgets sit just under the hard caps; the boot guard below refuses anything above.
    cmc_rate_limit_rpm: int = Field(default=30, alias="CMC_RATE_LIMIT_RPM")
    cmc_daily_credit_budget: int = Field(
        default=9000, alias="CMC_DAILY_CREDIT_BUDGET"
    )  # soft, < 10k/day
    cmc_monthly_credit_budget: int = Field(
        default=290000, alias="CMC_MONTHLY_CREDIT_BUDGET"
    )  # soft, < 300k/mo
    cmc_max_retries: int = Field(default=3, alias="CMC_MAX_RETRIES")
    cmc_max_wait_s: float = Field(
        default=8.0, alias="CMC_MAX_WAIT_S"
    )  # rate-stall ceiling (never blocks the poll)
    cmc_disk_cache: bool = Field(
        default=True, alias="CMC_DISK_CACHE"
    )  # last-good payload survives a restart
    # Market-intelligence fetchers (global-metrics, trending, categories, daily OHLCV…).
    # OFF by default → the agent is byte-for-byte unchanged until you opt in.
    cmc_intel_enabled: bool = Field(default=False, alias="CMC_INTEL_ENABLED")
    # On-chain (DEX) WS ingestion: the streamer also subscribes to the onchain@* channels
    # (token_metric/holders/liquidity/token_agg/transaction) for the BNB-chain universe
    # on a SEPARATE connection, harvesting buy/sell pressure, unique traders, holder concentration,
    # liquidity + whale flow. OFF by default → the contest-critical 4h bar feed is byte-identical.
    cmc_onchain_enabled: bool = Field(default=False, alias="CMC_ONCHAIN_ENABLED")
    onchain_whale_usd: float = Field(
        default=10000.0, alias="ONCHAIN_WHALE_USD"
    )  # min swap USD = "whale"

    # --- Strategy data levers (the CMC signal → decision wiring). Each is OFF/no-op at its default
    # so the validated path is byte-identical until enabled; the contest entry points turn the
    # high-value ones ON. See strategy/market_signals.py + docs/cmc_candles.md. ---
    alloc_mom_multi_w: float = Field(
        default=0.0, alias="ALLOC_MOM_MULTI_W"
    )  # multi-window CMC momentum rank weight
    alloc_flow_w: float = Field(
        default=0.0, alias="ALLOC_FLOW_W"
    )  # on-chain buy/sell flow rank tilt weight
    alloc_min_vol_usd: float = Field(
        default=0.0, alias="ALLOC_MIN_VOL_USD"
    )  # drop rank candidates below this 24h vol
    alloc_max_top10_pct: float = Field(
        default=0.0, alias="ALLOC_MAX_TOP10_PCT"
    )  # holder-concentration cap (0 = off)
    alloc_liq_brake: float = Field(
        default=0.0, alias="ALLOC_LIQ_BRAKE"
    )  # deploy-cap haircut on net liquidity outflow
    alloc_sector_tilt: float = Field(
        default=0.0, alias="ALLOC_SECTOR_TILT"
    )  # sector-rotation cap tilt weight (0 = off)
    # A/B flags: let the richer CMC signals DRIVE trades. Both default OFF, so the
    # validated contest path stays bit-for-bit identical until promoted after a SIM A/B.
    cmc_regime_enhanced: bool = Field(default=False, alias="CMC_REGIME_ENHANCED")
    alloc_universe_tilt: bool = Field(default=False, alias="ALLOC_UNIVERSE_TILT")
    # Enhanced-regime term weights (relative influence in the score mean; 0 disables a term).
    alloc_regime_w_dominance: float = Field(default=1.0, alias="ALLOC_REGIME_W_DOMINANCE")
    alloc_regime_w_mktcap: float = Field(default=1.0, alias="ALLOC_REGIME_W_MKTCAP")
    alloc_regime_w_fng_mom: float = Field(default=1.0, alias="ALLOC_REGIME_W_FNG_MOM")
    # CMC technical-analysis lever (RSI/MACD/EMA). PROVEN to lift risk-penalized PnL +
    # cut worst-week DD via `make ab_regime` (ta_cap/ta_rank/enhanced+ta PASS); the cap
    # term reads CMC's pre-computed daily TA live, computed locally for the backtest. A/B
    # flag, default OFF — SIM-first, contest path bit-for-bit unchanged until promoted.
    alloc_ta_enabled: bool = Field(default=False, alias="ALLOC_TA_ENABLED")
    alloc_ta_w_cap: float = Field(default=1.0, alias="ALLOC_TA_W_CAP")  # TA term in the deploy cap
    alloc_ta_w_rank: float = Field(default=1.0, alias="ALLOC_TA_W_RANK")  # TA tilt on the ranking
    # CMC Agent Hub — the Data MCP (12 tools incl. pre-computed RSI/MACD/EMA) + a COMPOSED
    # market-overview skill built on those tools (CMC's hosted Skills Marketplace has no
    # callable tool endpoint — see cmc_agent_hub.py / scripts/probe_agent_hub.py). Read-only
    # data (the SAME Startup key authenticates the MCP via X-CMC-MCP-API-KEY). Default OFF;
    # LIVE reads CMC's authoritative TA via MCP (local technicals.py is the backtest +
    # fallback). cmc_skill_regime gates the skill's risk-budget as a LIVE-only, forward-
    # validated cap modulator.
    cmc_mcp_enabled: bool = Field(default=False, alias="CMC_MCP_ENABLED")
    cmc_skill_regime: bool = Field(default=False, alias="CMC_SKILL_REGIME")
    cmc_mcp_url: str = Field(default="https://mcp.coinmarketcap.com/mcp", alias="CMC_MCP_URL")
    # Extra CMC Data-MCP tools wired into the composed market-overview skill's risk budget
    # (and surfaced on the dashboard). Each is its own A/B lever, default OFF, never raises,
    # only effective while CMC_SKILL_REGIME folds the skill budget into the deploy cap. SIM-
    # first: validate via scripts/validate_allocator.py before any LIVE promotion.
    cmc_mktcap_ta: bool = Field(default=False, alias="CMC_MKTCAP_TA")  # total-mktcap TA term
    cmc_deriv_brake: bool = Field(
        default=False, alias="CMC_DERIV_BRAKE"
    )  # leverage/funding fragility brake
    cmc_deriv_brake_w: float = Field(
        default=0.4, alias="CMC_DERIV_BRAKE_W"
    )  # max budget haircut at full stress
    cmc_macro_guard: bool = Field(
        default=False, alias="CMC_MACRO_GUARD"
    )  # de-risk into high-impact macro events
    cmc_macro_guard_hours: float = Field(default=36.0, alias="CMC_MACRO_GUARD_HOURS")
    cmc_macro_guard_haircut: float = Field(default=0.15, alias="CMC_MACRO_GUARD_HAIRCUT")
    cmc_quotes_xcheck: bool = Field(
        default=False, alias="CMC_QUOTES_XCHECK"
    )  # CMC quotes + ID-resolution proof
    cmc_news_enabled: bool = Field(
        default=False, alias="CMC_NEWS_ENABLED"
    )  # show latest news headlines
    cmc_news_brake: bool = Field(
        default=False, alias="CMC_NEWS_BRAKE"
    )  # optional negative-headline brake
    cmc_news_brake_w: float = Field(default=0.1, alias="CMC_NEWS_BRAKE_W")
    # Gasless trades via MegaFuel, keeping TWAK as the SOLE signer (pillar-2 safe).
    # Only effective if the real `twak swap` exposes a sponsored/gasless flag — verify
    # with `twak swap --help`, then set twak_gasless=true and twak_gasless_flag to the
    # exact flag the CLI accepts. Inert (native-gas) by default; never guesses on-chain.
    twak_gasless: bool = Field(default=False, alias="TWAK_GASLESS")
    twak_gasless_flag: str = Field(default="--gasless", alias="TWAK_GASLESS_FLAG")
    # Explicit per-swap slippage tolerance on a LIVE execute (verified `--slippage <pct>`
    # via `twak swap --help`; the CLI default is "1"). Passing it explicitly removes the
    # silent reliance on that default and lets us tighten below 1% to cut sandwich
    # exposure. Default 1.0 == today's implicit behavior (byte-identical). Set
    # twak_slippage_flag="" to disable appending the flag entirely.
    twak_slippage_pct: float = Field(default=1.0, alias="TWAK_SLIPPAGE_PCT")
    twak_slippage_flag: str = Field(default="--slippage", alias="TWAK_SLIPPAGE_FLAG")
    # Strategy selector — picks the registered PortfolioStrategy to run
    # (ictbot.strategy.registry). Empty (default) DERIVES the locked behavior from
    # ALLOC_ADAPTIVE: "" -> "momentum_adaptive" (adaptive on) / "momentum" (adaptive
    # off), so an unset operator runs the bit-for-bit contest default. New long-only-
    # spot strategies (e.g. "dual_momentum") are opt-in via STRATEGY_NAME on the SIM
    # track — they only reach LIVE after clearing the acceptance gate (engine/acceptance.py).
    strategy_name: str = Field(default="", alias="STRATEGY_NAME")
    # ZERO-CEX firewall. When true, the running strategy + its execution sizing MUST
    # source every price/candle from CoinMarketCap — any CEX path (cmc.fetch_4h ->
    # Binance/Bybit) RAISES loudly instead of silently falling back to exchange data.
    # This is the contest config for the CMC-native arm (momentum_cmc). Default false
    # keeps the locked momentum_adaptive arm + all dev/CI paths bit-for-bit. Requires
    # CMC_INTEL_ENABLED=true (the CMC 4h seed needs CMC daily OHLCV) — boot-guarded below.
    cmc_only: bool = Field(default=False, alias="CMC_ONLY")
    # FREE_DATA — CoinMarketCap is no longer available, so the agent sources every live read from
    # FREE, keyless public APIs: Binance klines (4h + daily candles), alternative.me (Fear & Greed),
    # and DexScreener (DEX price/liquidity/volume). The CMC-named seams (fear_greed, cmc_price,
    # fetch_cmc_4h, cmc_intel.daily_ohlcv) transparently route to these when set — no downstream
    # change. Default True (the operative mode); the free branches are gated `free_data and not
    # cmc_only`, so the CMC_ONLY firewall config (if ever re-enabled with a key) is untouched.
    free_data: bool = Field(default=True, alias="FREE_DATA")
    alloc_deploy_cap: float = Field(
        default=0.60, alias="ALLOC_DEPLOY_CAP"
    )  # static fallback / manual override
    alloc_lookback: int = 120  # momentum ranking horizon (4h bars)
    alloc_top_k: int = 5  # tokens held (sweep-picked: best multi-token that clears the DD gate)
    alloc_vol_lookback: int = 30  # inverse-vol sizing window
    alloc_rebal_bars: int = 6  # rebalance cadence (6 x 4h = daily)
    alloc_start_usdt: float = 1000.0  # sim starting NAV
    # Regime-adaptive deployment (src/ictbot/strategy/regime_score.py). When
    # alloc_adaptive=true the cap is NOT static — it scales with a live risk-on
    # score (basket breadth + trend + vol, plus live Fear&Greed) into the
    # participatory band [floor, ceiling]. Deploys more in a risk-on week, pulls
    # to cash in a falling/high-vol one. The per-token cash filter still applies.
    alloc_adaptive: bool = Field(default=True, alias="ALLOC_ADAPTIVE")
    alloc_cap_floor: float = Field(default=0.35, alias="ALLOC_CAP_FLOOR")
    alloc_cap_ceiling: float = Field(default=0.80, alias="ALLOC_CAP_CEILING")
    alloc_breadth_ma: int = 50  # SMA window for the breadth term
    # ACTIVE stance: when False (contest default), the agent ALWAYS deploys the
    # top-k by RELATIVE momentum (deployment scaled by the regime cap) instead of
    # sitting in cash whenever absolute momentum is negative — so it actually
    # trades every rebalance. True = the risk-first cash filter (cash in downtrends).
    alloc_abs_filter: bool = Field(default=False, alias="ALLOC_ABS_FILTER")
    # Deposit-aware re-baselining: when an external transfer (operator gas top-up, etc.)
    # changes the on-chain balance, the recon drift is treated as CAPITAL — the campaign
    # anchor + HWM shift by the net USD flow so a deposit is NOT counted as trading PnL and
    # cannot misfire the profit-lock / drawdown halt. Default True; False = the prior
    # behavior (deposits inflate NAV/PnL). See run_allocator `_tick` deposit guard.
    alloc_deposit_aware: bool = Field(default=True, alias="ALLOC_DEPOSIT_AWARE")
    # Native-gas reserve (BNB): on LIVE, a rebalance never sells the gas token below this
    # many units, so it can't drain the BNB the wallet needs to pay for its OWN swaps and
    # strand the book un-tradeable (BNB is both gas AND a contest token). The reserved BNB
    # is still counted in NAV — it's the agent's asset, just not sellable. ~0.004 BNB covers
    # several days of BSC swap gas. SIM ignores it (no real gas). 0 = off (legacy behavior).
    alloc_gas_reserve_bnb: float = Field(default=0.004, alias="ALLOC_GAS_RESERVE_BNB")
    # The LIVE client reads on-chain balances via the keyless Multicall3 path (ictbot.api.onchain)
    # instead of the `twak balance` CLI, whose hosted RPC backend is flaky (NETWORK_ERROR) and makes
    # a tick wedge for HOURS while it retries through the outage. The Multicall3 read is one bounded
    # batched call that fails fast, so a degraded RPC skips the tick instead of freezing the daemon.
    # twak still signs/executes SWAPS — only the read SOURCE changes. CEX-free (pure balanceOf) so it
    # stays CMC_ONLY-safe. Default True; set False to revert to the legacy twak-CLI read path. SIM is
    # unaffected (SimTwakClient holds an in-memory ledger).
    alloc_balance_via_multicall3: bool = Field(default=True, alias="ALLOC_BALANCE_VIA_MULTICALL3")
    # ---- Live on-chain wallet read (dashboard "real funds" card) ----------
    # Read-only + KEYLESS: the dashboard shows the TRADING wallet's actual BSC
    # holdings (native BNB + tokens) so judges see real money next to the SIM PnL.
    # Balances come from a single Multicall3 round-trip; USD is priced CMC-first
    # (pillar 1) with an on-chain Chainlink fallback. No secret needed — it uses a
    # PUBLIC BSC RPC on the cloud, so it stays zero-secret on Render.
    onchain_reads_enabled: bool = Field(default=True, alias="ONCHAIN_READS_ENABLED")
    # Optional explicit public RPC for the cloud read. Empty -> the keyed
    # BSC_RPC_HTTPS_URL (local) or built-in public fallbacks (publicnode/llama).
    onchain_bsc_rpc_url: str = Field(default="", alias="ONCHAIN_BSC_RPC_URL")

    # Mission Control dashboard: which track to serve — "sim" (paper forward run,
    # pre-contest) or "live" (the real contest track). Flip to live for 06-22.
    dashboard_journal: Literal["sim", "live"] = Field(default="sim", alias="DASHBOARD_JOURNAL")
    # Live dashboard refresh: a shared token that gates the `/api/ingest/snapshot` WRITE endpoint
    # so the local live tick can PUSH a fresh snapshot to the deployed (read-only) API after each
    # rebalance — without a git push / image rebuild. Empty ⇒ ingest DISABLED (default-deny): the
    # endpoint 503s, so an unconfigured deploy can't be written to. Gates dashboard data only — no
    # wallet/signing secret. The API serves a pushed snapshot only while its `served_at` is within
    # `pushed_snapshot_ttl_h` (else it falls back to the baked seed).
    ingest_token: str = Field(default="", alias="INGEST_TOKEN")
    pushed_snapshot_ttl_h: float = Field(default=48.0, alias="PUSHED_SNAPSHOT_TTL_H")
    # --- Scalability (read tier) -------------------------------------------------------------- #
    # All default to the single-instance, zero-dependency behavior the contest runs on; the
    # non-default values are the paid/multi-instance levers, flipped via env only. See SCALABILITY.md.
    #
    # Pushed-snapshot store backend. "file" (default) = the local atomic-write file every replica
    # would have its OWN copy of (correct only for ONE instance). "redis" = a shared store so N
    # read replicas all see the same pushed snapshot (prerequisite for numInstances>1). Any redis
    # error degrades to the same None the file path returns — never breaks the read surface.
    snapshot_store: Literal["file", "redis"] = Field(default="file", alias="SNAPSHOT_STORE")
    redis_url: str = Field(default="", alias="REDIS_URL")  # e.g. rediss://…  (secret — env-only)
    # In-process micro-cache TTL for the heavy journal-rebuild snapshot() fallback, so cache-miss
    # bursts / stale-while-revalidate refreshes don't each re-parse 500 journal lines + rebuild ~18
    # cards. Small enough that a fresh ingest is visible within one poll. 0 disables the cache.
    snapshot_cache_ttl_s: float = Field(default=2.0, alias="SNAPSHOT_CACHE_TTL_S")
    # Edge/CDN cache window (seconds) advertised on GET /api/* via Cache-Control s-maxage. Collapses
    # N viewers × every-4s-poll into ~1 origin hit per window once an edge (Vercel proxy / Cloudflare)
    # sits in front of the API. 0 disables emitting cache headers.
    edge_smaxage_s: int = Field(default=3, alias="EDGE_SMAXAGE_S")
    # --- Scalability (write tier) ------------------------------------------------------------- #
    # Allocator singleton lock. "flock" (default) = local fcntl lock — guards two ticks on ONE host
    # (correct for the contest's single operator machine). "redis" ADDS a shared single-key lease
    # (REDIS_URL) so the allocator can never run active-active across hosts even if accidentally
    # double-launched. The allocator is single-writer by design and is NEVER load-balanced — this is
    # a backstop, not an HA mechanism. See SCALABILITY.md §6.
    allocator_lock: Literal["flock", "redis"] = Field(default="flock", alias="ALLOCATOR_LOCK")
    # Cross-host lease TTL (seconds): a ceiling well above a real tick so a crashed holder self-heals.
    allocator_lock_ttl_s: int = Field(default=900, alias="ALLOCATOR_LOCK_TTL_S")
    # Forward-gated strategy auto-selector (scripts/strategy_evaluator.py). It scores DQ-safe arms on
    # risk-adjusted forward performance and recommends the best, with anti-chasing hysteresis. SIM is
    # auto-driven (proves it on the paper book); LIVE is RECOMMEND-ONLY by default — `auto_apply_live`
    # (default False) is the opt-in to also rewrite the live STRATEGY_NAME. `margin` = min score edge a
    # challenger must beat the incumbent by; `sustain` = consecutive evals it must hold before promotion.
    strategy_auto_select_sim: bool = Field(default=True, alias="STRATEGY_AUTO_SELECT_SIM")
    strategy_auto_apply_live: bool = Field(default=False, alias="STRATEGY_AUTO_APPLY_LIVE")
    strategy_select_margin: float = Field(default=0.02, alias="STRATEGY_SELECT_MARGIN")
    strategy_select_sustain: int = Field(default=2, alias="STRATEGY_SELECT_SUSTAIN")
    strategy_select_forward_min_days: float = Field(
        default=5.0, alias="STRATEGY_SELECT_FORWARD_MIN_DAYS"
    )

    # ---- Contest trade-floor (>=7 trades to be ranked) ------------------
    # The agent rebalances daily and usually clears 7 swaps/week, but a flat-regime
    # week could fall short — that's an instant disqualification. So we TRACK
    # cumulative swaps and, if behind pace within `trade_floor_lookahead_days` of
    # contest_end, AUTO-ENSURE the floor with bounded round-trip "FLOOR_NUDGE" swaps
    # (~0 NAV impact). Active only inside [contest_start, contest_end].
    contest_start: str = Field(default="2026-06-22", alias="CONTEST_START")
    contest_end: str = Field(default="2026-06-28", alias="CONTEST_END")
    trade_floor_min: int = Field(default=7, alias="TRADE_FLOOR_MIN")
    trade_floor_lookahead_days: float = Field(default=2.0, alias="TRADE_FLOOR_LOOKAHEAD_DAYS")
    # Brief also says ">=1 trade/day": when on, `run_allocator.py
    # --ensure-daily-floor` (cron'd near end-of-day UTC, contest window only)
    # banks ONE ~0-NAV-impact round-trip if the day would otherwise end with
    # zero swaps. Default OFF — pre-contest sim never nudges.
    trade_floor_daily: bool = Field(default=False, alias="TRADE_FLOOR_DAILY")
    trade_floor_daily_deadline_utc: int = Field(default=22, alias="TRADE_FLOOR_DAILY_DEADLINE_UTC")
    # Trade-floor token ROTATION: round-robin the floor nudge across the whole universe so every
    # token is touched over the contest week (vs. always nudging the largest holding). Contest-only,
    # ~0-NAV-impact; does NOT change the momentum allocation or the validated backtest path. Off ->
    # legacy largest-holding nudge.
    trade_floor_rotate: bool = Field(default=True, alias="TRADE_FLOOR_ROTATE")

    # ---- PnL campaign: profit-lock ratchet (2026-06-13) ------------------
    # Locks the GOOD path: once cumulative return (NAV vs the persisted
    # campaign anchor, set via `run_allocator.py --anchor-nav`) reaches
    # `trigger`, the ratchet ARMS and trails the running peak. Giving back
    # `trail` from that peak — never below `min_keep` over the anchor —
    # flattens to USDT and sets `profit_locked` (a SEPARATE flag from the
    # drawdown `halted`: --resume never re-opens a banked campaign;
    # --unlock-profit does). Reaching `bank` banks the campaign outright.
    # Default OFF: the validated baseline path is bit-for-bit unchanged
    # unless PROFIT_LOCK_ENABLED is set (campaign .env).
    profit_lock_enabled: bool = Field(default=False, alias="PROFIT_LOCK_ENABLED")
    profit_lock_trigger: float = Field(default=0.05, alias="PROFIT_LOCK_TRIGGER")
    profit_lock_trail: float = Field(default=0.03, alias="PROFIT_LOCK_TRAIL")
    profit_lock_min_keep: float = Field(default=0.03, alias="PROFIT_LOCK_MIN_KEEP")
    profit_lock_bank: float = Field(default=0.10, alias="PROFIT_LOCK_BANK")

    # ---- MSS timeframe (Box 3 of canonical flow) ------------------------
    # "poi"   — MSS runs on the 3m poi_df (spec). Slower, fewer false
    #           positives than the 1m default; matches "3M Confirmation"
    #           in the documented flow.
    # "entry" — MSS runs on the 1m entry_df (legacy pre-Phase-B). Use to
    #           reproduce backtests authored before this knob existed.
    mss_timeframe: Literal["entry", "poi"] = "poi"

    # ---- FVG sequence gate (Box 4 of canonical flow) --------------------
    # True  — MFVG must form on a bar strictly later than the MSS bar
    #         (spec). Catches the FVG-then-MSS sequence the legacy code
    #         accepted as a valid setup but ICT canon does not.
    # False — Legacy behaviour: FVG is accepted regardless of when it
    #         formed relative to MSS. Use to reproduce pre-Phase-C runs.
    require_fvg_after_mss: bool = True

    # ---- MFVG retest gate (Box 5 of canonical flow) ---------------------
    # True  — a later bar's CLOSE must fall inside the MFVG range before
    #         entry fires (spec). Strictest of the three retest
    #         definitions; matches what ICT educators teach.
    # False — Legacy: entry can fire on the FVG print alone. Use to
    #         reproduce pre-Phase-D runs.
    require_mfvg_retest: bool = True

    # ---- HTF/LTF bias alignment gate (Phase E) --------------------------
    # True (default) — refuse to fire when 4h HTF bias and 15m LTF bias
    #                  disagree. Stops the "short into bullish momentum"
    #                  pattern that killed the first live run (21/21
    #                  closed SELLs, 5.6% win rate, see plan §Context).
    # False          — pre-Phase-E behaviour: gate only on HTF bias,
    #                  ignore LTF entirely. Use to reproduce old runs
    #                  or when running synthetic tests that don't
    #                  bother to align both frames.
    require_bias_alignment: bool = Field(default=True, alias="REQUIRE_BIAS_ALIGNMENT")

    # ---- Fixed SL/TP distance overrides (Phase E winner) ----------------
    # WFO 2026-06-05 on BTC 10k bars w/ slope + bias-alignment gate found
    # the winning config at sl=0.005 / tp=0.025 (1:5 RR), TEST expectancy
    # +1.05R/trade. Old default 1:3 RR (tp=0.015) is statistically worse
    # at the realised 38.9% win rate. Override per-deployment via env so
    # different markets can tune independently.
    sl_frac: float = Field(default=0.005, alias="SL_FRAC")
    tp_frac: float = Field(default=0.015, alias="TP_FRAC")

    # ---- Fix 9.A (plan: Phase 9 per-token completeness) -----------------
    # Per-pair SL/TP overrides. Default None falls back to the global
    # sl_frac / tp_frac. Token is derived from the pair string:
    # "BTC/USDT:USDT" → SL_FRAC_BTC. Use `get_sl_frac(pair)` /
    # `get_tp_frac(pair)` to read with fallback.
    #
    # The single global default is correct only when all configured
    # pairs share the same volatility regime. BTC (~2-3 % daily ATR),
    # ETH (~3 %), SOL (~4-5 %), XRP (~3-5 %) clearly don't — a 0.5 % SL
    # is noise on SOL while a 2.5 % TP is rarely reached on low-vol
    # majors. The WFO per-pair refresh in `scripts/wfo_per_pair.py`
    # produces the winners that operators promote into `.env`.
    # (Phase 11: PAXG was dropped after the WFO returned `no edge`;
    # its env field was removed in the same commit.)
    sl_frac_btc: float | None = Field(default=None, alias="SL_FRAC_BTC")
    sl_frac_eth: float | None = Field(default=None, alias="SL_FRAC_ETH")
    sl_frac_sol: float | None = Field(default=None, alias="SL_FRAC_SOL")
    sl_frac_xrp: float | None = Field(default=None, alias="SL_FRAC_XRP")
    tp_frac_btc: float | None = Field(default=None, alias="TP_FRAC_BTC")
    tp_frac_eth: float | None = Field(default=None, alias="TP_FRAC_ETH")
    tp_frac_sol: float | None = Field(default=None, alias="TP_FRAC_SOL")
    tp_frac_xrp: float | None = Field(default=None, alias="TP_FRAC_XRP")

    # ---- Fix 12.A (plan: Phase 12 per-pair POI tolerance) ---------------
    # Per-pair POI tap tolerance overrides. Same shape as Phase 9.A's
    # SL/TP family. Default None falls back to global poi_tap_tolerance
    # (0.005). The Phase 9.A WFO scoreboard at 10k bars × rr2plus grid
    # showed the winning POI tolerance varying 0.0015 → 0.01 across
    # pairs — a 7× spread that no single global captures cleanly.
    # Operators promote winners from data/wfo/per_pair_<date>.json
    # into `.env`; defaults stay None so unset behaviour is the
    # pre-Phase-12 baseline.
    poi_tap_tolerance_btc: float | None = Field(default=None, alias="POI_TAP_TOLERANCE_BTC")
    poi_tap_tolerance_eth: float | None = Field(default=None, alias="POI_TAP_TOLERANCE_ETH")
    poi_tap_tolerance_sol: float | None = Field(default=None, alias="POI_TAP_TOLERANCE_SOL")
    poi_tap_tolerance_xrp: float | None = Field(default=None, alias="POI_TAP_TOLERANCE_XRP")

    def _pair_token(self, pair: str) -> str:
        """Extract the base-asset token from a ccxt pair string.

        "BTC/USDT:USDT" → "BTC". Robust against unknown shapes — returns
        an empty string when the input doesn't contain '/'.
        """
        if not pair or "/" not in pair:
            return ""
        return pair.split("/", 1)[0].upper()

    def get_sl_frac(self, pair: str) -> float:
        """Per-pair SL fraction with global fallback. Unknown pair →
        global default (so adding a 6th pair doesn't break — it just
        inherits SL_FRAC until an override is wired)."""
        token = self._pair_token(pair)
        override = getattr(self, f"sl_frac_{token.lower()}", None)
        return float(override) if override is not None else float(self.sl_frac)

    def get_tp_frac(self, pair: str) -> float:
        """Per-pair TP fraction with global fallback."""
        token = self._pair_token(pair)
        override = getattr(self, f"tp_frac_{token.lower()}", None)
        return float(override) if override is not None else float(self.tp_frac)

    def get_poi_tap_tolerance(self, pair: str) -> float:
        """Fix 12.A — per-pair POI tap tolerance with global fallback.

        Unknown pair → global `poi_tap_tolerance` (0.005). Same shape
        as `get_sl_frac` / `get_tp_frac` so the strategy reads them
        identically per-pair."""
        token = self._pair_token(pair)
        override = getattr(self, f"poi_tap_tolerance_{token.lower()}", None)
        return float(override) if override is not None else float(self.poi_tap_tolerance)

    # ---- POI frame (Box 2 of canonical flow) ----------------------------
    # "htf_then_poi" — try 4h POI first, fall back to 3m on WAITING.
    #                  Spec-recommended pragmatic default.
    # "htf"          — strict 4h POI only.
    # "poi"          — legacy 3m POI only. Pre-Phase-F behaviour.
    poi_frame: Literal["poi", "htf", "htf_then_poi"] = "htf_then_poi"

    # ---- SL/TP anchoring ------------------------------------------------
    # "fixed"      — legacy: SL/TP from sl_frac/tp_frac (or ATR multipliers
    #                if sl_atr_mult/tp_atr_mult are set). Bit-for-bit
    #                identical to behaviour before structural anchoring
    #                was added.
    # "structural" — Box 7/8 of the canonical flow: SL anchored to the
    #                MFVG edge, TP1 = 1:N RR off that real R distance,
    #                TP2 = next unbroken liquidity level. Falls back to
    #                "fixed" for the bracket if MFVG range / liquidity
    #                can't be computed on the bar.
    sl_anchor: Literal["fixed", "structural"] = "fixed"
    # 1:N RR for TP1 when sl_anchor=structural. Default 2.0 matches the
    # canonical flow ("TP1 = 1:2 RR"). The legacy fixed path still uses
    # tp_frac so changing this doesn't move existing configs.
    structural_tp1_rr: float = 2.0

    # ---- POI premium/discount filter (docs/findings_artifact_diff.md) ---
    # 0..1 fib level the OB midpoint must respect on the recent
    # `fib_lookback_bars`-bar swing leg. BULLISH wants discount (below the
    # level), BEARISH wants premium (above it). None = off (legacy).
    # Empirical validation via engine.wfo recommended before flipping the
    # default; the knob exists to make that prototype-on-a-branch run
    # one env change instead of a code edit.
    fib_filter: float | None = None
    fib_lookback_bars: int = 20

    # ---- Live-trading kill switch ---------------------------------------
    # Phase 8 wires execution. Until then this is read-only insurance:
    # nothing in the codebase places real orders even when True.
    enable_live_trading: bool = False

    # ---- Phase B — Shadow router (legacy ictbot) ----------------
    # When True, the scanner wraps the live router in a ShadowRouter that
    # runs a parallel PaperBroker leg on every signal. The live broker
    # places real orders; the shadow leg is a paper simulation against
    # the same result dict. Metrics (shadow_fill_slippage_bps, shadow_r_
    # delta, shadow_diverged_total) compare the two. Default False
    # preserves existing single-router behaviour.
    shadow_mode: bool = Field(default=False, alias="SHADOW_MODE")
    # Risk per LIVE trade as a fraction of equity. Used by the live leg of
    # the shadow router (and ignored when SHADOW_MODE=false). Defaults to
    # 0.0005 (0.05 %) — tightest safe size for Phase B mainnet shadow.
    # The shadow leg keeps using RISK_PCT (default 0.005) so the paper
    # ledger mirrors "normal-size" expectations.
    risk_pct_live: float = Field(default=0.0005, alias="RISK_PCT_LIVE")
    # Risk per paper trade (shadow + standalone PaperBroker). Pre-existed
    # as a constant in the scanner; promoted to a real setting so the
    # shadow leg and CLI report can read the same value.
    risk_pct: float = Field(default=0.005, alias="RISK_PCT")

    # ---- Fix 2.E (plan: live P&L clean-up) — entry-slippage handling -----
    # When True, after the market entry fills the live broker shifts SL
    # and TP by the same dollar offset as `(actual_avg - strategy_entry)`,
    # preserving the intended sl_frac / tp_frac risk distance regardless
    # of how far the market moved between signal print and fill. Off =
    # legacy behaviour (SL/TP placed at the strategy's pre-computed
    # prices, which silently shrinks the effective risk distance when
    # the entry slips against you).
    re_anchor_bracket: bool = Field(default=True, alias="RE_ANCHOR_BRACKET")
    # Hard upper bound on entry slippage. If `abs(actual_avg -
    # strategy_entry) / strategy_entry * 10_000 > this`, the broker
    # emergency-flattens the position immediately and re-raises as
    # LiveTradingDisabled so the router journals a clean
    # `REJECTED (slippage_exceeded)` instead of holding a bad fill.
    # 30 bps default is roughly 3x the WFO expectancy's per-trade edge —
    # holding a fill that slipped further is almost always a losing
    # trade in expectation, especially on the thin Binance testnet book.
    max_entry_slippage_bps: float = Field(default=30.0, alias="MAX_ENTRY_SLIPPAGE_BPS")

    # ---- Telegram heartbeat ----------------------------------------------
    # Send a full per-pair card pack to Telegram every N completed scan
    # cycles. 0 = disabled (default — only BUY/SELL signals get a TG
    # message). 1 = every cycle. Useful while iterating on the bot so
    # you can see live state without waiting for a setup to fire.
    tg_heartbeat_every_n_cycles: int = 0

    # ---- Telegram session gate ------------------------------------------
    # Limits TG noise to the times when an ICT setup actually has a
    # statistical edge: London / NY killzones. When True (default),
    # heartbeats + near-miss alerts are suppressed outside session.
    # BUY/SELL real fires bypass the gate (every fire is conf=100 → see
    # tg_min_confidence_bypass) and arrive with a clear "off-session"
    # disclaimer prepended to the message.
    tg_in_session_only: bool = True
    # Confidence value (0..100) that overrides the session gate. A
    # result with confidence >= this fires even off-session, with the
    # disclaimer attached. 100 = "only fires that satisfy every gate";
    # lower to also let high-but-not-perfect setups through.
    tg_min_confidence_bypass: int = 100

    # ---- Phase C — TG inline-button confirm-then-fire -------------------
    # Phase C (legacy ictbot). Default OFF preserves existing
    # behaviour: BUY/SELL signals route directly through the broker.
    # When ON, each BUY/SELL is DM'd to the operator with [✅ Trade] /
    # [❌ Skip] inline buttons; the trade only fires if the operator
    # clicks Trade within tg_confirm_timeout_s. Requires
    # `pip install -e ".[tg]"` (python-telegram-bot).
    tg_confirm_mode: bool = Field(default=False, alias="TG_CONFIRM_MODE")
    # Seconds before a pending signal auto-expires (no fire). 180 s
    # default = enough time to read the card and decide; short enough
    # that a stale card doesn't fire on a moved market.
    tg_confirm_timeout_s: int = Field(default=180, alias="TG_CONFIRM_TIMEOUT_S")
    # Numeric Telegram user id of the SOLE operator allowed to click
    # the inline buttons. Get yours by messaging @userinfobot. 0 = unset.
    # When tg_confirm_mode=true and this is 0, the scanner refuses to
    # start (see Settings.__init__ validation below) — accepting clicks
    # from any user would be a footgun.
    tg_operator_user_id: int = Field(default=0, alias="TG_OPERATOR_USER_ID")

    # ---- Phase D — Tiered autonomy + discipline caps --------------------
    # Confidence threshold that splits AUTO from CONFIRM tiers in
    # scanner._route_signal. Signals with confidence >= this go straight
    # to the live router; signals with confidence < this AND
    # tg_confirm_mode=true are routed to the TG confirm-button flow; else
    # they are dropped (jlog'd). Default 100 preserves existing behaviour:
    # only perfect-score signals auto-fire. Lower to e.g. 75 to opt in to
    # confirm-button DM for high-but-not-perfect setups.
    auto_execute_min_confidence: int = Field(default=100, alias="AUTO_EXECUTE_MIN_CONFIDENCE")
    # Hard daily cap on LIVE bracket placements per UTC day. The cap
    # reads today's count from the journal (route="live", entry in
    # {BUY,SELL}, today's UTC date) so it survives restarts naturally.
    # Default 3 = three real attempts/day max even if the strategy fires
    # more. Set to a large number (e.g. 9999) to effectively disable.
    max_live_trades_per_day: int = Field(default=3, alias="MAX_LIVE_TRADES_PER_DAY")
    # Hard ceiling on RISK_PCT_LIVE. If risk_pct_live exceeds this when
    # enable_live_trading=true, the bot refuses to boot. Prevents an
    # accidental 0.05 or 0.1 in .env from torching the account. Default
    # 0.001 (0.1 %) = double the recommended 0.0005 (0.05 %) leaves
    # headroom for cautious dialling-up.
    max_live_risk_per_trade_pct: float = Field(default=0.001, alias="MAX_LIVE_RISK_PER_TRADE_PCT")
    # ---- Phase D — TG operator commands ---------------------------------
    # Independent of tg_confirm_mode. When True, the same PTB Application
    # also handles /status, /journal, /kill, /resume, /pause, /whoami
    # from the operator. Lets the operator run the bot from their phone
    # without enabling the confirm-button gate. See plan §TG.
    tg_commands_mode: bool = Field(default=False, alias="TG_COMMANDS_MODE")

    # ---- Fix 5.C (plan: Phase 5 Tier 2 — visibility) --------------------
    # When True, every position close (WIN / LOSS / BE / CLOSED) emits a
    # TG message with the realised R + USDT + fee. Default True since
    # closes are low-frequency events. Set to false to silence (e.g.
    # during high-volume backfill runs).
    tg_notify_on_close: bool = Field(default=True, alias="TG_NOTIFY_ON_CLOSE")
    # Fix 5.E: surface signal rejections to TG at the Nth occurrence per
    # (pair, reason) pair. 0 = off (default — rejections stay in the
    # journal only). Useful in early-validation to confirm caps are
    # firing, but throttled to avoid firehose on max_open_positions.
    tg_notify_rejections_every: int = Field(default=0, alias="TG_NOTIFY_REJECTIONS_EVERY")

    # Fix 5.H (plan: Phase 5 Tier 4 — cleanup): promote the previously
    # hard-coded MaxOpenPositions(1) cap to an env override.
    # Fix 9.B (plan: Phase 9): default raised from 1 → 3 so the system
    # can hold up to 3 of the 5 pairs simultaneously. The previous
    # default starved 4 of 5 pairs whenever one position was open.
    # MaxConcurrentSameDirection below prevents the failure mode of 3
    # correlated SELLs stacking during a crypto-wide downtrend.
    # `on_reconnect` (Fix 5.B) rebuilds one stub per net position, so
    # >1 simultaneous positions on the SAME pair (under one-way mode)
    # aren't possible anyway — this cap counts across pairs.
    max_open_positions: int = Field(default=3, alias="MAX_OPEN_POSITIONS")
    # Fix 9.B: anti-correlation gate. Max simultaneous positions on the
    # same side (BUY or SELL). Default 2 of 3 means: BUY+BUY+SELL or
    # SELL+SELL+BUY allowed, but not BUY+BUY+BUY or SELL+SELL+SELL. Set
    # to 0 to disable (becomes a no-op, no rejection on side).
    max_same_direction: int = Field(default=2, alias="MAX_SAME_DIRECTION")

    # Fix 9.C (plan: Phase 9 per-token completeness): when True (default),
    # the broker refuses to construct if margin mode / leverage doesn't
    # take per pair. Pre-fix path silently logged and continued; that
    # masked silent-failure scenarios where one pair ran with the wrong
    # leverage carried over from a prior session. Set to false to fall
    # back to log-and-continue for pairs that legitimately can't be
    # normalized (rare; document the venue's stance before disabling).
    strict_pair_init: bool = Field(default=True, alias="STRICT_PAIR_INIT")

    # Fix 13.A (plan: Phase 13 tunable risk caps): hardcoded 1R limit on
    # DailyLossLimit (scanner.py) promoted to an env knob. 1R is the
    # historical default and the right number for typical Phase E
    # sizing (RISK_PCT_LIVE=0.0005 × $10k = $5/trade); paper / shadow
    # runs may want wider, mainnet may want tighter. Boot guard below
    # refuses on `<= 0` (no cap = misconfiguration).
    daily_loss_limit_r: float = Field(default=1.0, alias="DAILY_LOSS_LIMIT_R")

    # Fix 13.B (plan: Phase 13 tunable risk caps): hardcoded 5%
    # MaxDrawdown limit (scanner.py) promoted to an env knob. Boot
    # guard refuses on `<= 0` or `>= 1.0` (0 = no cap; ≥ 100% is
    # nonsensical — 50% is already extreme).
    max_drawdown_frac: float = Field(default=0.05, alias="MAX_DRAWDOWN_FRAC")

    # Phase 14 — Near-price dedup. Reject a new entry when a recently
    # PLACED entry on the same (pair, side) sits within
    # `near_price_dedup_bps` of the current price AND inside
    # `near_price_dedup_window_s`. Catches the noise-print failure
    # mode where the analyzer re-emits the same conf=100 signal each
    # cycle at near-identical price (XRP saw 4 prints within 70 s on
    # 2026-06-06, every print would otherwise pyramid the position).
    # 0 on either field disables. Default 20 bps × 900 s ≈ $3 on ETH
    # 1550 / $0.002 on XRP 1.08 / $0.12 on SOL 62 / $120 on BTC 60 k
    # over a 15 min window — wide enough to swallow same-setup noise
    # but tight enough that a re-setup hours later (price has
    # actually drifted) routes freely.
    near_price_dedup_bps: float = Field(default=20.0, alias="NEAR_PRICE_DEDUP_BPS")
    near_price_dedup_window_s: float = Field(default=900.0, alias="NEAR_PRICE_DEDUP_WINDOW_S")


settings = Settings()


# ---- Phase C startup validation ----------------------------------------
# TG_CONFIRM_MODE=true with no TG_OPERATOR_USER_ID set would mean ANY
# Telegram user who saw the button could trigger trades. Refuse to boot
# rather than silently accept that.
if settings.tg_confirm_mode and not settings.tg_operator_user_id:
    raise RuntimeError(
        "TG_CONFIRM_MODE=true requires TG_OPERATOR_USER_ID to be set "
        "(numeric Telegram user id of the operator). Get yours by "
        "messaging @userinfobot."
    )

# Same validation for the commands-only path.
if settings.tg_commands_mode and not settings.tg_operator_user_id:
    raise RuntimeError(
        "TG_COMMANDS_MODE=true requires TG_OPERATOR_USER_ID to be set "
        "(numeric Telegram user id of the operator). Get yours by "
        "messaging @userinfobot."
    )

# ---- Phase D startup validation ----------------------------------------
# A finger-slip on RISK_PCT_LIVE (e.g. 0.05 instead of 0.0005) would size
# every live trade at 50x intended. Refuse to boot if live trading is on
# AND risk per trade exceeds the hard ceiling.
#
# Fix 2.D (plan: live P&L clean-up) — RISK_PCT_LIVE now applies whenever
# the live router is built, regardless of SHADOW_MODE. The single guard
# below is therefore the authoritative protection for live sizing; the
# old conditional that only fired on shadow=on has been removed at
# scanner.py:_build_router. RISK_PCT remains the paper / shadow-leg
# value and intentionally has no boot guard so paper backtests can
# still run at 0.5 % without complaint.
if settings.enable_live_trading and settings.risk_pct_live > settings.max_live_risk_per_trade_pct:
    raise RuntimeError(
        f"RISK_PCT_LIVE ({settings.risk_pct_live}) exceeds "
        f"MAX_LIVE_RISK_PER_TRADE_PCT ({settings.max_live_risk_per_trade_pct}); "
        "refusing to boot. Lower RISK_PCT_LIVE in .env or raise the ceiling "
        "intentionally if you know what you're doing."
    )

# Fix 5.I (plan: Phase 5 Tier 4 — cleanup): pre-boot API-key sanity check.
# Refuse to start if ENABLE_LIVE_TRADING=true AND the venue's API key/secret
# is empty. Catches a finger-slip earlier than the first ccxt call, where
# the error would be a PermissionDenied with a 50-line traceback.
#
# Audit E1: scope this to the CEX (ICT/ccxt) live path only. The live trading agent
# trades via TWAK and uses NO CEX, so when TWAK_MODE=live it must NOT demand a CEX
# key — that path is covered by the dedicated TWAK guard below. Without this scoping,
# cleaning legacy CEX keys out of .env for submission would break the live boot.
if settings.enable_live_trading and settings.twak_mode != "live":
    _venue_to_creds = {
        "binance": (
            settings.binance_api_key,
            settings.binance_api_secret,
            "BINANCE_API_KEY",
            "BINANCE_API_SECRET",
        ),
        "delta": (
            settings.delta_api_key,
            settings.delta_api_secret,
            "DELTA_API_KEY",
            "DELTA_API_SECRET",
        ),
    }
    _key, _secret, _key_name, _secret_name = _venue_to_creds.get(
        settings.exchange.lower(), ("", "", "<API_KEY>", "<API_SECRET>")
    )
    if not _key or not _secret:
        raise RuntimeError(
            f"ENABLE_LIVE_TRADING=true with EXCHANGE={settings.exchange!r} "
            f"but {_key_name} or {_secret_name} is empty in .env. Refusing "
            f"to boot — set both before re-enabling live trading."
        )

# Fix 13.A (plan: Phase 13 tunable risk caps): DAILY_LOSS_LIMIT_R must be
# positive — zero or negative means the cap is effectively disabled, which
# is almost always a misconfiguration. Catch at boot rather than letting
# the silent-no-cap behaviour ship to production.
if settings.daily_loss_limit_r <= 0:
    raise RuntimeError(
        f"DAILY_LOSS_LIMIT_R must be > 0; got {settings.daily_loss_limit_r}. "
        "A zero or negative loss limit disables the cap — set DAILY_LOSS_LIMIT_R "
        "to a positive R-multiple (1.0 = historical default)."
    )

# Fix 13.B (plan: Phase 13 tunable risk caps): MAX_DRAWDOWN_FRAC must be
# in (0, 1). Zero / negative disables the cap; ≥ 1.0 (100 % drawdown
# allowed) is nonsensical — even 50 % drawdown means halving the account.
if not (0 < settings.max_drawdown_frac < 1.0):
    raise RuntimeError(
        f"MAX_DRAWDOWN_FRAC must be in (0, 1); got {settings.max_drawdown_frac}. "
        "Default is 0.05 (5%). Setting to 0 disables the cap; setting ≥ 1.0 "
        "is nonsensical."
    )

# ---- PnL campaign: profit-lock ratchet sanity ----------------------------
# trigger < bank (arm before banking), trail in (0,1), and min_keep strictly
# below trigger — a min_keep >= trigger would place the lock floor AT/ABOVE
# the arming NAV and insta-flatten the moment the ratchet arms.
if not (0 < settings.profit_lock_trigger < settings.profit_lock_bank):
    raise RuntimeError(
        f"PROFIT_LOCK_TRIGGER must be in (0, PROFIT_LOCK_BANK); got "
        f"trigger={settings.profit_lock_trigger}, bank={settings.profit_lock_bank}."
    )
if not (0 < settings.profit_lock_trail < 1.0):
    raise RuntimeError(f"PROFIT_LOCK_TRAIL must be in (0, 1); got {settings.profit_lock_trail}.")
if not (0 <= settings.profit_lock_min_keep < settings.profit_lock_trigger):
    raise RuntimeError(
        f"PROFIT_LOCK_MIN_KEEP must be in [0, PROFIT_LOCK_TRIGGER); got "
        f"min_keep={settings.profit_lock_min_keep}, trigger={settings.profit_lock_trigger}."
    )

# ---- Strategy auto-selector hysteresis sanity --------------------------
# sustain < 1 would promote a challenger on its very first appearance (no anti-chasing).
if settings.strategy_select_sustain < 1:
    raise RuntimeError(
        f"STRATEGY_SELECT_SUSTAIN must be >= 1; got {settings.strategy_select_sustain}. "
        "It's the consecutive-eval count a challenger must hold before promotion (default 2)."
    )

# ---- Live trading: TWAK live-mode credential guard ---------------------
# TWAK_MODE=live means the allocator signs REAL BSC swaps — refuse to boot
# without the TWAK API creds (a finger-slip would otherwise fail cryptically
# on the first swap). Inert at the default TWAK_MODE=sim.
if settings.twak_mode == "live" and not (settings.twak_access_id and settings.twak_hmac_secret):
    raise RuntimeError(
        "TWAK_MODE=live requires TWAK_ACCESS_ID + TWAK_HMAC_SECRET in .env "
        "(the allocator signs real BSC swaps in live mode). Set both, or use "
        "TWAK_MODE=sim / `run_allocator.py --mode sim`."
    )

# ---- Identity: gasless-heartbeat sponsor guard -------------------------
# A per-tick gasless write through MegaFuel needs the user's keyed sponsor
# endpoint — without NODEREAL_API_KEY the SDK would silently hit the PUBLIC
# paymaster (the exact bug that left the user's NodeReal app at zero requests).
# Fail loud instead of writing to the wrong sponsor. Inert when heartbeats off.
if (
    settings.agent_heartbeat_enabled
    and settings.agent_use_paymaster
    and not settings.nodereal_api_key
):
    raise RuntimeError(
        "AGENT_HEARTBEAT_ENABLED=true with AGENT_USE_PAYMASTER=true requires "
        "NODEREAL_API_KEY in .env — otherwise gasless writes hit the PUBLIC MegaFuel "
        "endpoint and your keyed sponsor app records nothing. Set NODEREAL_API_KEY, "
        "or disable AGENT_HEARTBEAT_ENABLED."
    )

# ---- CMC commercial-tier budget guard ----------------------------------
# The Startup plan HARD-caps at 300k credits/mo, ~10k/day, 30 req/min. A finger-slip
# raising a SOFT budget above the hard cap would let the agent blow the commercial cap
# (overage billing) — refuse to boot, mirroring the MAX_DRAWDOWN_FRAC guard.
if settings.cmc_daily_credit_budget > 10_000:
    raise RuntimeError(
        f"CMC_DAILY_CREDIT_BUDGET ({settings.cmc_daily_credit_budget}) exceeds the CMC "
        "Startup plan's ~10k/day hard cap. Lower it (default 9000)."
    )
if settings.cmc_monthly_credit_budget > 300_000:
    raise RuntimeError(
        f"CMC_MONTHLY_CREDIT_BUDGET ({settings.cmc_monthly_credit_budget}) exceeds the CMC "
        "Startup plan's 300k/mo hard cap. Lower it (default 290000)."
    )
if settings.cmc_rate_limit_rpm > 30:
    raise RuntimeError(
        f"CMC_RATE_LIMIT_RPM ({settings.cmc_rate_limit_rpm}) exceeds the CMC Startup plan's "
        "30 req/min hard cap. Lower it (default 30)."
    )

# ---- Zero-CEX firewall: two-flag dependency -----------------------------
# CMC_ONLY makes the running arm refuse any CEX fallback, sourcing candles from the
# CMC 4h stream + the cold-start CMC-daily seed. That seed (seed_cmc_4h_from_daily /
# daily_close_matrix) needs CMC DAILY OHLCV, which is gated by CMC_INTEL_ENABLED. With
# CMC_ONLY on but intel off, the seed returns nothing → the matrix stays thin → every
# tick skips forever (silent brick). Fail loud at boot instead. Inert at the default.
if settings.cmc_only and not settings.cmc_intel_enabled:
    raise RuntimeError(
        "CMC_ONLY=true requires CMC_INTEL_ENABLED=true: the CMC-native candle seed "
        "(seed_cmc_4h_from_daily / daily_close_matrix) needs CMC daily OHLCV, which is "
        "gated by CMC_INTEL_ENABLED. Enable both for the contest config, or unset CMC_ONLY."
    )

# ---- Dashboard live-track safety (WARN, never raise) --------------------
# DASHBOARD_JOURNAL picks which ledger the API serves ("sim" paper vs "live" contest). Leaving
# it on "sim" DURING the contest window would silently show stale paper PnL to judges. This is a
# config slip, not a fatal error (the read-only deploy must still boot), so we WARN to stderr —
# only inside [contest_start, contest_end] and only when it isn't already "live". Never raises.
if settings.dashboard_journal != "live" and not os.environ.get("ICTBOT_SKIP_DOTENV"):
    try:
        from datetime import datetime, timezone

        _start = datetime.fromisoformat(settings.contest_start).replace(tzinfo=timezone.utc)
        _end = datetime.fromisoformat(settings.contest_end).replace(tzinfo=timezone.utc)
        if _start <= datetime.now(timezone.utc) <= _end:
            import sys as _sys

            print(
                f"[settings] WARNING: inside the contest window ({settings.contest_start}.."
                f"{settings.contest_end}) but DASHBOARD_JOURNAL={settings.dashboard_journal!r} — "
                "the dashboard is serving the SIM ledger, not the LIVE contest track. Set "
                "DASHBOARD_JOURNAL=live and redeploy (make deploy_dashboard).",
                file=_sys.stderr,
            )
    except Exception:
        pass  # date parse / env quirk must never break boot


# ---- Canonical-flow kill-switch enforcement ----------------------------
# When CANONICAL_FLOW=off, force legacy values regardless of per-field
# env vars. This is the "panic button" — one env var change reverts
# every Phase A flag at once so production can be rolled back fast.
if settings.canonical_flow == "off":
    settings.poi_engine = "min_max"
    settings.bias_engine = "sma"
    settings.strategy_mode = "fade"
    settings.sl_anchor = "fixed"
    settings.mss_timeframe = "entry"
    settings.require_fvg_after_mss = False
    settings.require_mfvg_retest = False
    settings.poi_frame = "poi"


# ---- Backwards-compat module-level constants ----------------------------
# Anything that already does `from ictbot.settings import X` keeps working.
TELEGRAM_TOKEN = settings.telegram_token
TELEGRAM_CHAT_ID = settings.telegram_chat_id

PAIRS = list(settings.pairs)
UI_PAIRS = list(settings.ui_pairs)

SIGNAL_FILE = JOURNAL_DIR / "last_signal.json"
NEAR_MISS_FILE = JOURNAL_DIR / "last_near_miss.json"
JOURNAL_FILE = JOURNAL_DIR / "signals.json"
CURVE_FILE = RUNS_DIR / "backtest_curve.json"

HTF_TIMEFRAME = settings.htf_timeframe
BIAS_TIMEFRAME = settings.bias_timeframe
POI_TIMEFRAME = settings.poi_timeframe
ENTRY_TIMEFRAME = settings.entry_timeframe

POI_TAP_TOLERANCE = settings.poi_tap_tolerance
FEE_PER_SIDE = settings.fee_per_side
SLIPPAGE_PER_SIDE = settings.slippage_per_side
TRAIL_BREAKEVEN_R = settings.trail_breakeven_r

POI_ENGINE = settings.poi_engine
BIAS_ENGINE = settings.bias_engine

# News-blackout gate (Forex Factory). 0 = off.
NEWS_BLACKOUT_MINUTES = settings.news_blackout_minutes
NEWS_BLACKOUT_COUNTRIES = tuple(settings.news_blackout_countries)
NEWS_BLACKOUT_IMPACTS = tuple(settings.news_blackout_impacts)
NEWS_ALERT_WINDOW_MIN = settings.news_alert_window_min
STRATEGY_MODE = settings.strategy_mode

ENABLE_LIVE_TRADING = settings.enable_live_trading

TG_HEARTBEAT_EVERY_N_CYCLES = settings.tg_heartbeat_every_n_cycles
TG_IN_SESSION_ONLY = settings.tg_in_session_only
TG_MIN_CONFIDENCE_BYPASS = settings.tg_min_confidence_bypass

SL_ANCHOR = settings.sl_anchor
STRUCTURAL_TP1_RR = settings.structural_tp1_rr
FIB_FILTER = settings.fib_filter
FIB_LOOKBACK_BARS = settings.fib_lookback_bars
MSS_TIMEFRAME = settings.mss_timeframe
REQUIRE_FVG_AFTER_MSS = settings.require_fvg_after_mss
REQUIRE_MFVG_RETEST = settings.require_mfvg_retest
POI_FRAME = settings.poi_frame
REQUIRE_BIAS_ALIGNMENT = settings.require_bias_alignment
SL_FRAC = settings.sl_frac
TP_FRAC = settings.tp_frac

EXCHANGE = settings.exchange
BINANCE_TESTNET = settings.binance_testnet

# Phase B shadow router
SHADOW_MODE = settings.shadow_mode
RISK_PCT_LIVE = settings.risk_pct_live
RISK_PCT = settings.risk_pct
SHADOW_JOURNAL_FILE = JOURNAL_DIR / "shadow_signals.json"

# Phase C TG confirm flow
TG_CONFIRM_MODE = settings.tg_confirm_mode
TG_CONFIRM_TIMEOUT_S = settings.tg_confirm_timeout_s
TG_OPERATOR_USER_ID = settings.tg_operator_user_id

# Phase D — tiered autonomy + caps
AUTO_EXECUTE_MIN_CONFIDENCE = settings.auto_execute_min_confidence
MAX_LIVE_TRADES_PER_DAY = settings.max_live_trades_per_day
MAX_LIVE_RISK_PER_TRADE_PCT = settings.max_live_risk_per_trade_pct
TG_COMMANDS_MODE = settings.tg_commands_mode
# Fix 5.C / 5.E (Phase 5 Tier 2)
TG_NOTIFY_ON_CLOSE = settings.tg_notify_on_close
TG_NOTIFY_REJECTIONS_EVERY = settings.tg_notify_rejections_every
MAX_OPEN_POSITIONS = settings.max_open_positions
MAX_SAME_DIRECTION = settings.max_same_direction
STRICT_PAIR_INIT = settings.strict_pair_init
DAILY_LOSS_LIMIT_R = settings.daily_loss_limit_r
MAX_DRAWDOWN_FRAC = settings.max_drawdown_frac
# Phase 14 — near-price dedup
NEAR_PRICE_DEDUP_BPS = settings.near_price_dedup_bps
NEAR_PRICE_DEDUP_WINDOW_S = settings.near_price_dedup_window_s
PAUSED_UNTIL_FILE = DATA_DIR / "PAUSED_UNTIL"
