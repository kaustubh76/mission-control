"""
Typed JSON contract for the dashboard API.

These pydantic models are the single source of truth for the response shapes; the
React client's `web/src/api/types.ts` mirrors them 1:1. Fields that depend on
runtime data the agent may not have produced yet are Optional with safe defaults,
so an empty/young journal still yields a valid (if sparse) response rather than a
500. Nested dynamic maps (weights/target/balances) are `dict[str, float]`.
"""

from __future__ import annotations

from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# /api/health
# --------------------------------------------------------------------------- #
class HealthOut(BaseModel):
    ok: bool = True
    heartbeat_age_s: float | None = None
    last_beat_iso: str | None = None
    mode: str = "sim"  # settings.twak_mode
    journal_mode: str = "sim"  # settings.dashboard_journal (track being shown)
    journal_mismatch: bool = False  # true if showing a different track than the agent runs
    live_trading_enabled: bool = False
    kill_switch_engaged: bool = False


# --------------------------------------------------------------------------- #
# /api/identity  (ERC-8004 profile, verbatim from identity.profile())
# --------------------------------------------------------------------------- #
class IdentityEndpoint(BaseModel):
    name: str
    endpoint: str
    version: str
    capabilities: list[str] = []


class IdentityOut(BaseModel):
    name: str
    network: str
    trading_wallet: str
    description: str
    endpoints: list[IdentityEndpoint] = []


# --------------------------------------------------------------------------- #
# /api/strategy
# --------------------------------------------------------------------------- #
class StrategyParams(BaseModel):
    top_k: int
    lookback: int
    cap_floor: float
    cap_ceiling: float
    rebal_bars: int


class StrategyOut(BaseModel):
    name: str = "momentum_adaptive"  # registered strategy id (default keeps old snapshots valid)
    summary: str
    tokens: list[str] = []  # full contest universe (canonical order)
    active: list[str] = []  # UI-toggled active subset; [] = unknown → client treats as "all"
    params: StrategyParams


# --------------------------------------------------------------------------- #
# /api/state
# --------------------------------------------------------------------------- #
class StateOut(BaseModel):
    hwm: float | None = None
    halted: bool = False
    halt_reason: str | None = None
    halt_ts: str | None = None
    nav: float | None = None
    balances: dict[str, float] = {}
    weights: dict[str, float] = {}
    cumulative_swaps: int = 0  # trades banked toward the contest floor
    trade_floor: int = 7  # the >=7-trade minimum
    profit_lock: dict | None = (
        None  # PnL-campaign ratchet status (armed/locked/cum_ret/…); None = no campaign anchor
    )


# --------------------------------------------------------------------------- #
# /api/nav  (equity curve + drawdown vs the contest caps)
# --------------------------------------------------------------------------- #
class NavPoint(BaseModel):
    ts: str
    nav: float


class DdPoint(BaseModel):
    ts: str
    dd: float


class Caps(BaseModel):
    team: float = 0.15  # our self-imposed ceiling
    dq: float = 0.30  # contest disqualification line
    configured: float = 0.05  # settings.max_drawdown_frac (the live halt)


class Drawdown(BaseModel):
    current: float = 0.0
    series: list[DdPoint] = []


class NavOut(BaseModel):
    curve: list[NavPoint] = []
    current_nav: float | None = None
    hwm: float | None = None
    drawdown: Drawdown = Drawdown()
    caps: Caps = Caps()


# --------------------------------------------------------------------------- #
# /api/regime
# --------------------------------------------------------------------------- #
class RegimeOut(BaseModel):
    regime_score: float | None = None
    fear_greed: int | None = None
    fear_greed_label: str = "unknown"
    deploy_cap: float | None = None
    stale: bool = False  # true when F&G is a cached/last-known fallback


# --------------------------------------------------------------------------- #
# /api/rebalances
# --------------------------------------------------------------------------- #
class TxRef(BaseModel):
    hash: str
    url: str


class RebalanceItem(BaseModel):
    ts: str
    event: str = "REBALANCE"
    mode: str = "sim"
    strategy: str | None = None  # registered strategy that produced this tick (None in old rows)
    # Data provenance — "cmc_4h" (CMC stream) | "cmc_daily" | "binance_4h". Declared here because
    # response_model=SnapshotOut SILENTLY STRIPS undeclared fields, so the UI would never see it
    # otherwise. None in pre-provenance rows. Lets the dashboard prove the decision is CMC-sourced.
    candle_source: str | None = None
    # 7d-tilt data source — "cmc_ws" (0-credit WS quote snapshot) | "rest" | None. Declared so the
    # response model doesn't strip it (proves the credit-saving read-through served the tilt).
    quote_source: str | None = None
    # CMC on-chain (DEX) per-token signals fed into the decision this tick: {SYM: {flow_ratio,
    # unique_traders, liquidity_usd, top10_pct, net_liquidity_usd, whale_net_usd, ...}}. None when
    # the on-chain feed is off/cold. Free-form dict — surfaced for observability (no UI panel yet).
    onchain_signals: dict | None = None
    nav_before: float | None = None
    nav_after: float | None = None
    n_swaps: int = 0
    n_swaps_total: int = 0
    n_failed: int = 0
    failed_swaps: list[dict] = []
    fees_usd: float = 0.0
    tx: list[TxRef] = []
    target: dict[str, float] = {}
    weights_after: dict[str, float] = {}
    rationale: str | None = None
    x402_dex: dict | None = None  # pillar-1 CMC AI Agent Hub read for this tick (or None)
    active_tokens: list[str] | None = None  # universe the tick ranked over (None = pre-toggle row)
    profit_lock: dict | None = (
        None  # PnL-campaign ratchet status (enabled/armed/locked/cum_ret/…); None = off
    )


class RebalancesOut(BaseModel):
    items: list[RebalanceItem] = []


# --------------------------------------------------------------------------- #
# /api/pillars  (three agent pillars: data/x402 · TWAK · identity/NodeReal)
# --------------------------------------------------------------------------- #
class X402Receipts(BaseModel):
    total: int = 0
    settled: int = 0
    spent_usdc: float = 0.0
    last_ts: str | None = None
    last_status: str | None = None


class CmcPillar(BaseModel):
    x402_enabled: bool = False
    pay_wallet: str | None = None
    base_usdc_balance: float | None = None
    receipts: X402Receipts = X402Receipts()
    last_dex: dict | None = None


class TwakPillar(BaseModel):
    mode: str = "sim"
    gasless: bool = False
    gasless_flag: str = "--gasless"
    cumulative_swaps: int = 0
    trade_floor: int = 7


class NodeRealPillar(BaseModel):
    api_key_set: bool = False
    network: str = "bsc"
    sdk_installed: bool | None = None
    use_paymaster: bool = True
    reachable: bool | None = None
    chain_id: int | None = None
    chain_ok: bool | None = None
    sponsorable: bool | None = None
    wallet: str | None = None
    nonce: int | None = None
    registry: str | None = None
    note: str | None = None
    agent_id: int = 0
    heartbeat_enabled: bool = False
    identity_wallet_bnb: float | None = None
    last_heartbeat_ok: bool | None = None
    last_heartbeat_tx: str | None = None
    last_heartbeat_ts: str | None = None
    last_heartbeat_error: str | None = None


class CommerceService(BaseModel):
    """What the agent ADVERTISES over ERC-8183 — the offering, anchored to its ERC-8004 identity."""

    name: str = "Market Regime Report"
    report_schema: str = "cmc-regime-report/v1"
    price: int = 0
    storage: str = "local"
    capabilities: list[str] = []
    provider: str | None = None
    agent_id: int = 0
    registry: str | None = None


class CommercePreview(BaseModel):
    """The live deliverable the agent would hand over right now — sourced from the latest tick."""

    ts: str | None = None
    strategy: str | None = None
    regime_score: float | None = None
    deploy_cap: float | None = None
    fear_greed: int | None = None
    fear_greed_label: str | None = None
    momentum_ranking: list[str] = []
    rationale: str | None = None


class ProvenanceBlock(BaseModel):
    """vlayer on-chain data-provenance attestation for the SOLD Market Regime Report (Web Proof / zkTLS).

    Read-only + key-free, so it is safe on the zero-secret dashboard deploy. `enabled` reflects the
    `VLAYER_ENABLED` flag + a configured verifier; the proof fields (`chain`..`attestation`) are
    populated only once an on-chain attestation exists — otherwise the UI shows a 'pending' state."""

    enabled: bool = False
    chain: str | None = None
    verifier: str | None = None
    agent: str | None = None
    fear_greed: int | None = None
    classification: str | None = None
    proven_at: str | None = None
    attestation: str | None = None  # "onchain" when proven, else None


class CommerceBlock(BaseModel):
    enabled: bool = False
    network: str = "bsc-testnet"
    jobs_created: int = 0
    jobs_funded: int = 0
    jobs_served: int = 0
    jobs_settled: int = 0
    jobs_pending_settle: int = 0  # served on-chain but not yet SETTLE'd (optimistic dispute window)
    last_settle_status: str | None = None  # "settled" | "deferred" — latest finalization outcome
    revenue_u: float = 0.0
    last_ts: str | None = None
    last_event: str | None = None
    last_deliverable_hash: str | None = None
    last_deliverable_url: str | None = None  # IPFS URI of the last served deliverable (public, clickable)
    last_tx: str | None = None
    service: CommerceService = CommerceService()
    preview: CommercePreview | None = None
    # True only when a LOCAL operator run can sign BOTH sides (provider + a distinct buyer keystore).
    # The read-only cloud deploy has no signing key, so this is False there → the "create job" button
    # renders disabled (operator-only). Drives the UI; never exposes a secret.
    can_create: bool = False
    # vlayer data-provenance attestation for the sold report (read-only; None/enabled=False when off).
    provenance: ProvenanceBlock | None = None


class PillarsOut(BaseModel):
    cmc: CmcPillar = CmcPillar()
    twak: TwakPillar = TwakPillar()
    nodereal: NodeRealPillar = NodeRealPillar()
    commerce: CommerceBlock = CommerceBlock()


# --------------------------------------------------------------------------- #
# /api/wallet  (LIVE on-chain holdings — the "real funds" card, vs the SIM NAV)
# --------------------------------------------------------------------------- #
class WalletAsset(BaseModel):
    symbol: str
    amount: float
    usd: float | None = None  # amount * price, None if unpriced
    price: float | None = None  # USD price used
    source: str | None = None  # "cmc" | "chainlink" | "stable" | None
    is_gas: bool = False  # native BNB (the trade-gas buffer)


class WalletOut(BaseModel):
    ok: bool = False
    address: str | None = None
    explorer_url: str | None = None
    block: int | None = None
    network: str = "bsc"
    assets: list[WalletAsset] = []
    total_usd: float | None = None
    priced_source: str | None = None  # dominant price source, for the header chip
    gas_bnb: float | None = None
    gas_low: bool = False  # native BNB below the thin-gas threshold
    x402_budget_usdc: float | None = None  # identity wallet's Base USDC (x402 budget)
    served_at: str | None = None
    note: str | None = None  # set when degraded (e.g. "no reachable BSC RPC")


# --------------------------------------------------------------------------- #
# /api/market-intel  (CMC Startup-tier market intelligence)
# --------------------------------------------------------------------------- #
class GlobalMetrics(BaseModel):
    btc_dominance: float | None = None
    eth_dominance: float | None = None
    stablecoin_market_cap: float | None = None
    total_market_cap: float | None = None
    total_volume_24h: float | None = None
    altcoin_market_cap: float | None = None


class FngTrendPoint(BaseModel):
    ts: int
    value: int


class Mover(BaseModel):
    symbol: str | None = None
    name: str | None = None
    pct_24h: float | None = None


class Movers(BaseModel):
    gainers: list[Mover] = []
    losers: list[Mover] = []


class Category(BaseModel):
    name: str | None = None
    avg_price_change: float | None = None
    market_cap: float | None = None
    market_cap_change: float | None = None
    num_tokens: int | None = None


class RegimeTerms(BaseModel):
    breadth: float | None = None
    trend: float | None = None
    vol_factor: float | None = None
    fng: float | None = None
    dominance: float | None = None
    mktcap: float | None = None
    fng_mom: float | None = None
    score: float | None = None


class MarketIntelOut(BaseModel):
    enabled: bool = False
    global_metrics: GlobalMetrics | None = None
    fng_trend: list[FngTrendPoint] = []
    movers: Movers = Movers()
    categories: list[Category] = []
    regime_terms: RegimeTerms | None = None


# --------------------------------------------------------------------------- #
# /api/cmc-api  (CMC client telemetry — the credit-budget health card)
# --------------------------------------------------------------------------- #
class CmcApiOut(BaseModel):
    credits_today: int = 0
    daily_budget: int = 0
    credits_month: int = 0
    monthly_budget: int = 0
    req_count: int = 0
    last_status: int | None = None
    last_credit_count: int = 0  # credits the last CMC call cost (CMC-5: was emitted, now typed)
    rate_wait_total_s: float = 0.0
    rpm: int = 30
    near_cap_day: bool = False
    near_cap_month: bool = False
    healthy: bool = True
    key_set: bool = False


# --------------------------------------------------------------------------- #
# /api/rationale  (the "agent talks" feed)
# --------------------------------------------------------------------------- #
class RationaleItem(BaseModel):
    ts: str
    rationale: str


class RationaleOut(BaseModel):
    items: list[RationaleItem] = []


# --------------------------------------------------------------------------- #
# /api/snapshot → token_rotation  (per-token coverage; held vs ~0-NAV floor)
# --------------------------------------------------------------------------- #
class TokenRotationEntryOut(BaseModel):
    token: str
    touched: bool
    source: str  # held | nudged | both | none
    count: int  # journal appearances (held-ticks + floor nudges)
    last_ts: str | None = None


class TokenRotationOut(BaseModel):
    tokens: list[TokenRotationEntryOut] = []
    touched_count: int = 0
    total: int = 0
    held: list[str] = []  # momentum holdings (real allocation)
    nudged: list[str] = []  # contest-floor rotation (~0 NAV)


# --------------------------------------------------------------------------- #
# /api/agent-hub  (CMC Agent Hub exhibit — Data MCP + Skills + x402)
# --------------------------------------------------------------------------- #
class AgentHubMcp(BaseModel):
    calls: int = 0
    by_tool: dict[str, int] = {}
    # Full CMC Data-MCP catalog (12); by_tool holds the exercised subset. Without this field the
    # response_model would strip it from the serialized API, so the panel could never read it.
    tools_available: list[str] = []
    last_call_ts: int | None = None


class AgentHubSkill(BaseModel):
    skill_source: str | None = None  # "composed" (built on Data MCP) | "cmc-marketplace"
    risk_budget: float | None = None
    regime: str | None = None
    fear_greed: int | None = None
    btc_dominance: float | None = None
    mktcap_change_24h: float | None = None
    ta_breadth: dict | None = None
    headline: str | None = None
    narratives: list[str] = []
    tools_used: list[str] = []
    # Extra CMC Data-MCP reads the composed skill folded in (None unless the lever is on).
    derivatives: dict | None = None  # leverage/funding stress {stress, oi_change_24h, ...}
    mktcap_ta: dict | None = None  # total-mktcap TA {rsi14, macd_histogram, health, ...}
    next_macro_event: dict | None = None  # {title, event_date, hours_to, high_impact}
    quotes_cross_check: dict | None = None  # {sym: {price, pct_24h, volume_24h, ...}}
    top_news: list[dict] = []  # [{title, url, published_at}]


class AgentHubX402(BaseModel):
    total: int = 0
    settled: int = 0
    spent_usdc: float = 0.0
    last_ts: str | None = None
    last_status: str | None = None  # receipt status ("settled"/"failed"); was wrongly typed int


class MarketDataHubOut(BaseModel):
    """FREE Market Data Hub — the free-data-stack exhibit that replaces the CMC Agent Hub. `overview`
    and `dex` are free-form dicts (declared as `dict` so response_model doesn't strip the nested
    fields): overview = free composed market-overview; dex = {SYM: {price_usd, liquidity_usd,
    volume_24h, price_change_h24, txns_24h}}."""

    enabled: bool = False
    overview: dict | None = None
    dex: dict | None = None
    sources: list[str] = []


class AgentHubOut(BaseModel):
    mcp_enabled: bool = False
    ta_enabled: bool = False
    skill_enabled: bool = False
    x402_enabled: bool = False
    mcp: AgentHubMcp = AgentHubMcp()
    ta_health: float | None = None
    ta_source: str | None = None
    skill: AgentHubSkill | None = None
    x402: AgentHubX402 = AgentHubX402()
    # Live CMC WebSocket on-chain signals the agent harvested this tick (from the journal row →
    # env-independent on Render). Free-form per-token dict; declared so response_model doesn't strip
    # it: {SYM: {flow_ratio, liquidity_usd, top10_pct, whale_net_usd, net_liquidity_usd, ...}}.
    onchain_enabled: bool = False
    onchain: dict | None = None
    # CMC-native rotation levers acted on this tick (from the journal row → env-independent on Render):
    # sector rotation toward trending narratives + multi-window CMC momentum. Free-form so
    # response_model doesn't strip it: {trending: [...], sector_hits: [...], mom: {SYM: float}}.
    rotation_enabled: bool = False
    rotation: dict | None = None


class AgentHubPingSkill(BaseModel):
    risk_budget: float | None = None
    regime: str | None = None
    headline: str | None = None
    tools_used: list[str] = []


class AgentHubPingOut(BaseModel):
    """Result of a LIVE, on-demand probe of CMC's Agent Hub (made server-side at request time —
    proves the MCP + composed Skill genuinely work on the deploy, not seeded snapshot data)."""
    enabled: bool = False  # server has CMC_MCP_ENABLED + a key (can make live MCP calls)
    tools_live: int = 0  # tools returned by a LIVE `tools/list` right now
    sample_ok: bool = False  # a real `tools/call` (global-metrics) returned data
    last_error: str | None = None
    ts: str | None = None  # server timestamp of this live probe
    skill: AgentHubPingSkill | None = None  # freshly-computed composed market-overview Skill


# --------------------------------------------------------------------------- #
# /api/snapshot  (one poll → one render)
# --------------------------------------------------------------------------- #
class StrategyMenuItem(BaseModel):
    name: str
    summary: str = ""
    current: bool = False  # the strategy the SIM track is running
    alias_of: str | None = None  # underlying arm if this is a BNB_STRATEGY_0X alias
    survival: dict | None = None  # backtest gate verdict (validate_strategy --save-verdict)
    forward: dict | None = None  # forward-promotion verdict (forward_promote --save)
    stability: dict | None = None  # stability grade {grade, ts} (make stability)
    scoreboard: dict | None = (
        None  # backtest perf {total_return, win_rate(window), mean_ret, median_ret} — SCOREBOARD over survivors, NOT an edge claim
    )
    readiness: dict | None = (
        None  # contest-readiness rollup {state: ready|in_progress|not_ready|incumbent, note}
    )


class StrategiesOut(BaseModel):
    items: list[StrategyMenuItem] = []
    current: str = "momentum_adaptive"


class LiveArmOut(BaseModel):
    """The arm actually trading real money (or configured to), + its gate — distinct from the SIM
    Strategy Lab. survival/forward are pass-through dicts (their sub-keys aren't stripped)."""

    name: str = "momentum_adaptive"
    source: str = "default"  # "live journal" | "configured (STRATEGY_NAME)" | "default"
    candle_source: str | None = None
    survival: dict | None = None  # {passed, worst_week_dd, trades_per_week, ...}
    forward: dict | None = None  # {forward_eligible, status, ...}
    switchable: bool = True
    note: str | None = None


class EconomyPnLOut(BaseModel):
    """Agent-economy P&L. HEADLINE (net_economic_usd) = the TRADING PnL only (book NAV − anchor). x402
    (data cost, different wallet) and self-funded commerce (~−gas) are REAL on-chain activity but NOT
    trading results — surfaced as separate context fields, never summed into the headline."""

    trading_pnl_usd: float | None = None
    trading_nav_usd: float | None = None
    anchor_usd: float | None = None
    commerce_revenue_u: float | None = None
    commerce_revenue_usd: float | None = None
    commerce_self_funded: bool = False  # buyer + provider both operator wallets → integration proof, not revenue
    x402_spent_usd: float | None = None
    net_economic_usd: float | None = None  # == trading_pnl_usd (headline = strategy performance only)
    u_valuation_note: str | None = None
    gas_note: str | None = None
    commerce_note: str | None = None  # why self-funded commerce is excluded from PnL
    x402_note: str | None = None      # why x402 data cost is excluded from PnL


class ArmScoreOut(BaseModel):
    """One arm's risk-adjusted score + gate flags in the auto-selector ranking."""

    arm: str
    score: float | None = None
    dq_safe: bool = False
    forward_eligible: bool = False
    stability: str | None = None
    verdict: str | None = None


class HysteresisOut(BaseModel):
    """Anti-chasing state: how long the champion has held the slot + the promotion thresholds."""

    consecutive: int | None = None
    sustain: int | None = None
    margin: float | None = None
    incumbent_score: float | None = None
    champion_score: float | None = None


class EvaluationOut(BaseModel):
    """Forward-gated auto-selector verdict — the recommended LIVE arm + why (recommend-only by default)."""

    recommended: str | None = None
    current: str | None = None
    action: str | None = None  # SWITCH | STAY
    switch: bool = False
    reason: str | None = None
    apply_cmd: str | None = None
    auto_apply_live: bool = False
    hysteresis: HysteresisOut | None = None
    scores: list[ArmScoreOut] = []
    ts: str | None = None


class SchedulerOut(BaseModel):
    """Scheduler-health: ages of the last live tick + dd-watch, so a silent cron death is visible."""

    ok: bool = True
    live_tick_age_h: float | None = None
    live_tick_stale: bool = False
    dd_watch_age_m: float | None = None
    dd_watch_stale: bool = False
    note: str | None = None


class SnapshotOut(BaseModel):
    health: HealthOut
    identity: IdentityOut | None = None
    strategy: StrategyOut | None = None
    strategies: StrategiesOut | None = None  # the registry menu + verdicts (dashboard selector)
    live_arm: LiveArmOut | None = None  # the REAL live/contest arm + gate (distinct from the SIM lab)
    state: StateOut
    nav: NavOut
    regime: RegimeOut
    rebalances: RebalancesOut
    rationale: RationaleOut
    token_rotation: TokenRotationOut | None = None  # per-token coverage (held vs ~0-NAV floor)
    pillars: PillarsOut | None = None
    wallet: WalletOut | None = None  # LIVE on-chain real funds (vs the SIM NAV)
    market_intel: MarketIntelOut | None = None  # CMC Startup-tier market intelligence
    cmc_api: CmcApiOut | None = None  # CMC credit-budget + rate telemetry
    agent_hub: AgentHubOut | None = None  # CMC Agent Hub (MCP + Skills + x402) exhibit (legacy)
    market_data_hub: MarketDataHubOut | None = None  # live FREE-data-stack exhibit
    economy: EconomyPnLOut | None = None  # consolidated agent-economy P&L (one 'true position')
    auto_selector: EvaluationOut | None = None  # forward-gated arm auto-selector (recommend-only for live)
    scheduler: SchedulerOut | None = None  # scheduler health (last live-tick + dd-watch ages)
    served_at: str | None = None  # server clock at read time (data freshness)


# --------------------------------------------------------------------------- #
# Controls
# --------------------------------------------------------------------------- #
class SimTickOut(BaseModel):
    ok: bool
    rc: int | None = None
    message: str


class KillIn(BaseModel):
    engage: bool
    reason: str | None = None


class KillOut(BaseModel):
    ok: bool
    engaged: bool
    message: str


class TokensIn(BaseModel):
    active: list[str]


class TokensOut(BaseModel):
    ok: bool
    active: list[str] = []
    message: str = ""


class StrategySelectIn(BaseModel):
    strategy: str


class StrategySelectOut(BaseModel):
    ok: bool
    strategy: str = ""
    available: list[str] = []
    message: str = ""


class CommerceCreateJobIn(BaseModel):
    """Buyer request to create + fund a real ERC-8183 job our agent then serves (operator-local)."""

    description: str = "Give me your current CMC regime read + momentum ranking."
    amount: int | None = None  # payment-token units; None → settings.erc8183_service_price
    expiry_min: int = 20160  # 14d — MUST exceed the ~7d dispute window (else submit deadline is already past)


class CommerceCreateJobOut(BaseModel):
    ok: bool
    stage: str | None = None  # "fund-precheck" (shortfall) | "served" (loop ran) — distinguishes failure modes
    job_id: int | None = None
    status: str | None = None
    tx: str | None = None
    deliverable_hash: str | None = None
    deliverable_url: str | None = None
    buyer: str | None = None  # buyer wallet (surfaced so the operator can fund it)
    provider: str | None = None
    network: str | None = None
    amount: int | None = None
    token: str | None = None  # payment-token symbol (e.g. "U")
    token_address: str | None = None  # payment-token ERC-20 contract — what to fund on mainnet
    need: int | None = None  # set on insufficient-balance precheck
    have: int | None = None
    message: str = ""


class CommerceSettleIn(BaseModel):
    """Buyer request to FINALIZE served-but-unsettled ERC-8183 jobs (operator-local). `job_ids=None`
    → settle every job the ledger shows as awaiting finalization."""

    job_ids: list[int] | None = None


class CommerceSettleOut(BaseModel):
    ok: bool
    pending_before: int = 0  # how many jobs were attempted
    settled: list[dict] = []  # [{job_id, status}] — finalized this run
    deferred: list[dict] = []  # [{job_id, reason}] — dispute window still open (not an error)
    errors: list[dict] = []  # [{job_id, error}] — genuine failures
    network: str | None = None
    message: str = ""


class CommercePreviewIn(BaseModel):
    """Visitor/buyer request for a READ-ONLY preview of the agent's sellable Market Regime Report.
    No wallet, no chain action, no signing — works everywhere, including the zero-secret cloud."""

    description: str = "Give me your current CMC regime read + momentum ranking."


class CommercePreviewReportOut(BaseModel):
    """The genuine ERC-8183 deliverable (the live Market Regime Report), built read-only — exactly what
    a buyer receives, minus the on-chain escrow/settlement. Mirrors agent.regime_report.build_report."""

    ok: bool
    status: str | None = None  # "ok" | "degraded" (insufficient CMC history / decision error)
    issuer: str | None = None
    strategy: str | None = None
    regime_score: float | None = None
    deploy_cap: float | None = None
    ta_health: float | None = None  # MCP-authoritative basket TA health that tilts the deploy cap
    fear_greed: int | None = None
    momentum_ranking: list[str] = []
    target_weights: dict[str, float] = {}
    rationale: str | None = None
    cmc_sources: dict | None = None  # buyer-verifiable CMC provenance (pro_api/mcp_skill/mcp_ta)
    # Live composed-Skill signals (regime label, BTC dominance, mktcap 24h Δ, trending narratives,
    # headline, risk budget) so the deliverable surfaces genuinely live market intelligence.
    market_overview: dict | None = None
    # Live DexScreener DEX-native signals per token (price, liquidity, 24h volume, 24h change, txns) —
    # the free on-chain data the report ships now that CMC's DEX feeds are gone.
    dex_signals: dict | None = None
    ts: str | None = None
    message: str = ""
