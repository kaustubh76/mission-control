// Mirrors src/ictbot/api/schemas.py 1:1 — the typed JSON contract.

export interface Health {
  ok: boolean;
  heartbeat_age_s: number | null;
  last_beat_iso: string | null;
  mode: string;
  journal_mode: string;
  journal_mismatch: boolean;
  live_trading_enabled: boolean;
  kill_switch_engaged: boolean;
}

export interface IdentityEndpoint {
  name: string;
  endpoint: string;
  version: string;
  capabilities: string[];
}

export interface Identity {
  name: string;
  network: string;
  trading_wallet: string;
  description: string;
  endpoints: IdentityEndpoint[];
}

export interface StrategyParams {
  top_k: number;
  lookback: number;
  cap_floor: number;
  cap_ceiling: number;
  rebal_bars: number;
}

export interface Strategy {
  name?: string; // registered strategy id (absent in old snapshots → treat as momentum_adaptive)
  summary: string;
  tokens: string[]; // full contest universe (canonical order)
  active?: string[]; // UI-toggled active subset; [] or absent = treat as "all" (old snapshots lack it)
  params: StrategyParams;
}

export interface State {
  hwm: number | null;
  halted: boolean;
  halt_reason: string | null;
  halt_ts: string | null;
  nav: number | null;
  balances: Record<string, number>;
  weights: Record<string, number>;
  cumulative_swaps: number;
  trade_floor: number;
}

export interface NavPoint {
  ts: string;
  nav: number;
}

export interface DdPoint {
  ts: string;
  dd: number;
}

export interface Caps {
  team: number;
  dq: number;
  configured: number;
}

export interface Drawdown {
  current: number;
  series: DdPoint[];
}

export interface Nav {
  curve: NavPoint[];
  current_nav: number | null;
  hwm: number | null;
  drawdown: Drawdown;
  caps: Caps;
}

export interface Regime {
  regime_score: number | null;
  fear_greed: number | null;
  fear_greed_label: string;
  deploy_cap: number | null;
  stale: boolean;
}

export interface TxRef {
  hash: string;
  url: string;
}

export interface RebalanceItem {
  ts: string;
  event: string;
  mode: string;
  strategy?: string; // registered strategy that produced this tick (absent in old rows)
  candle_source?: string | null; // data provenance: "cmc_4h" | "cmc_daily" | "binance_4h" | null
  quote_source?: string | null; // 7d-tilt source: "cmc_ws" (0-credit WS snapshot) | "rest" | null
  onchain_signals?: Record<string, Record<string, number | null>> | null; // CMC on-chain DEX signals per token
  nav_before: number | null;
  nav_after: number | null;
  n_swaps: number;
  n_swaps_total: number;
  n_failed: number;
  failed_swaps: Array<Record<string, unknown>>;
  fees_usd: number;
  tx: TxRef[];
  target: Record<string, number>;
  weights_after: Record<string, number>;
  rationale: string | null;
  x402_dex: X402Dex | null;
  active_tokens?: string[] | null; // universe the tick ranked over (absent = pre-toggle row)
}

export interface X402Dex {
  q?: string;
  name?: string;
  symbol?: string;
  price_usd?: number;
  liquidity?: number;
}

export interface Rebalances {
  items: RebalanceItem[];
}

export interface RationaleItem {
  ts: string;
  rationale: string;
}

export interface RationaleFeed {
  items: RationaleItem[];
}

// ── Per-token rotation (mirrors token_rotation_card in reads.py) ───────────
// "Touched" = the token has been TRADED at all. Two honest sources, NEVER an edge claim:
//   held   = a real momentum top-k holding (appeared in weights_after > 0)
//   nudged = a ~0-NAV contest-floor round-trip (the rotation that reaches the rest of the 8)
export type RotationSource = "held" | "nudged" | "both" | "none";

export interface TokenRotationEntry {
  token: string;
  touched: boolean;
  source: RotationSource;
  count: number; // journal appearances (held-ticks + floor nudges)
  last_ts: string | null;
}

export interface TokenRotation {
  tokens: TokenRotationEntry[];
  touched_count: number;
  total: number;
  held: string[]; // momentum holdings (real allocation)
  nudged: string[]; // contest-floor rotation (~0 NAV)
}

// ── Three Track-1 pillars (mirrors PillarsOut in schemas.py) ──────────────
export interface X402Receipts {
  total: number;
  settled: number;
  spent_usdc: number;
  last_ts: string | null;
  last_status: string | null;
}

export interface CmcPillar {
  x402_enabled: boolean;
  pay_wallet: string | null;
  base_usdc_balance: number | null;
  receipts: X402Receipts;
  last_dex: X402Dex | null;
}

export interface TwakPillar {
  mode: string;
  gasless: boolean;
  gasless_flag: string;
  cumulative_swaps: number;
  trade_floor: number;
}

export interface NodeRealPillar {
  api_key_set: boolean;
  network: string;
  sdk_installed: boolean | null;
  use_paymaster: boolean;
  reachable: boolean | null;
  chain_id: number | null;
  chain_ok: boolean | null;
  sponsorable: boolean | null;
  wallet: string | null;
  nonce: number | null;
  registry: string | null;
  note: string | null;
  agent_id: number;
  heartbeat_enabled: boolean;
  identity_wallet_bnb?: number | null;
  last_heartbeat_ok?: boolean | null;
  last_heartbeat_tx?: string | null;
  last_heartbeat_ts?: string | null;
  last_heartbeat_error?: string | null;
}

export interface CommerceService {
  name: string;
  report_schema: string;
  price: number;
  storage: string;
  capabilities: string[];
  provider: string | null;
  agent_id: number;
  registry: string | null;
}

export interface CommercePreview {
  ts: string | null;
  strategy: string | null;
  regime_score: number | null;
  deploy_cap: number | null;
  fear_greed: number | null;
  fear_greed_label: string | null;
  momentum_ranking: string[];
  rationale: string | null;
}

export interface CommerceBlock {
  enabled: boolean;
  network: string;
  jobs_created: number;
  jobs_funded: number;
  jobs_served: number;
  jobs_settled: number;
  jobs_pending_settle: number; // served on-chain, not yet finalized (optimistic dispute window)
  last_settle_status?: string | null; // "settled" | "deferred" — latest finalization outcome
  revenue_u: number;
  last_ts: string | null;
  last_event: string | null;
  last_deliverable_hash: string | null;
  last_deliverable_url: string | null; // IPFS URI of the last served deliverable (clickable via ipfsUrl)
  last_tx: string | null;
  service?: CommerceService; // the advertised offering (real even at zero jobs)
  preview?: CommercePreview | null; // live deliverable preview from the latest tick
  can_create?: boolean; // true only when a LOCAL operator run can sign both sides (else button disabled)
  provenance?: VlayerProvenance | null; // vlayer on-chain data-provenance attestation (read-only)
}

// vlayer Web Proof (zkTLS) attestation that the sold report's CMC inputs are genuine — mirrors
// ProvenanceBlock in schemas.py. `enabled` distinguishes "off" from "on but not yet proven";
// the proof fields populate only once an on-chain attestation exists (attestation === "onchain").
export interface VlayerProvenance {
  enabled: boolean;
  chain?: string | null;
  verifier?: string | null;
  agent?: string | null;
  fear_greed?: number | null;
  classification?: string | null;
  proven_at?: string | null;
  attestation?: string | null; // "onchain" when proven, else null
}

// Result of POST /api/commerce/create-job — the real ERC-8183 loop (create→fund→serve→settle).
export interface CommerceCreateJobResult {
  ok: boolean;
  stage?: string | null; // "fund-precheck" | "served" — which point in the loop the result reflects
  job_id?: number | null;
  status?: string | null;
  tx?: string | null;
  deliverable_hash?: string | null;
  deliverable_url?: string | null;
  buyer?: string | null; // surfaced so the operator can fund it
  provider?: string | null;
  network?: string | null;
  amount?: number | null;
  token?: string | null; // payment-token symbol (e.g. "U")
  token_address?: string | null; // payment-token ERC-20 contract — what to fund on mainnet
  need?: number | null; // set on insufficient-balance precheck
  have?: number | null;
  message?: string;
}

// Result of POST /api/commerce/settle — finalize served-but-unsettled jobs once their dispute
// window has closed. `deferred` (window still open) is an expected outcome, not an error.
export interface CommerceSettleResult {
  ok: boolean;
  pending_before?: number;
  settled?: Array<{ job_id: number; status?: string }>;
  deferred?: Array<{ job_id: number; reason?: string }>;
  errors?: Array<{ job_id: number; error?: string }>;
  network?: string | null;
  message?: string;
}

// Result of POST /api/commerce/preview — the live Market Regime Report the agent sells, built READ-ONLY
// (no signing, no spend). Works everywhere incl. the zero-secret cloud deploy. Mirrors
// CommercePreviewReportOut in schemas.py.
export interface CommercePreviewReport {
  ok: boolean;
  status?: string | null; // "ok" | "degraded"
  issuer?: string | null;
  strategy?: string | null;
  regime_score?: number | null;
  deploy_cap?: number | null;
  ta_health?: number | null; // MCP-authoritative TA health folded into the deploy cap
  fear_greed?: number | null;
  momentum_ranking?: string[];
  target_weights?: Record<string, number>;
  rationale?: string | null;
  cmc_sources?: Record<string, unknown> | null; // buyer-verifiable CMC provenance
  // Live composed-Skill signals — the more-dynamic market intelligence behind the regime read.
  market_overview?: {
    skill_source?: string | null;
    risk_budget?: number | null;
    regime?: string | null;
    btc_dominance?: number | null;
    mktcap_change_24h?: number | null;
    headline?: string | null;
    narratives?: string[] | null;
  } | null;
  // Live DexScreener DEX-native signals per token (free on-chain data): price, liquidity, 24h volume,
  // 24h change, tx count. Populated under FREE_DATA. Mirrors dex_signals in schemas.py.
  dex_signals?: Record<string, DexSignal> | null;
  ts?: string | null;
  message?: string;
}

export interface DexSignal {
  price_usd?: number | null;
  liquidity_usd?: number | null;
  volume_24h?: number | null;
  price_change_h24?: number | null;
  txns_24h?: number | null;
  pct_7d?: number | null; // free Binance daily-klines 7d return (present when with_windows)
  pct_30d?: number | null;
}

// Free composed market-overview (alternative.me F&G + CoinGecko global + DexScreener breadth).
export interface MarketOverview {
  skill_source?: string | null;
  risk_budget?: number | null;
  regime?: string | null;
  fear_greed?: number | null;
  btc_dominance?: number | null;
  mktcap_change_24h?: number | null;
  narratives?: string[] | null;
  headline?: string | null;
  tools_used?: string[] | null;
  ta_breadth?: { tokens?: number | null; up_24h?: number | null; ta_health?: number | null } | null;
}

// Live FREE-data-stack exhibit — mirrors MarketDataHubOut in schemas.py.
export interface MarketDataHub {
  enabled: boolean;
  overview?: MarketOverview | null;
  dex?: Record<string, DexSignal> | null;
  sources?: string[];
}

export interface Pillars {
  cmc: CmcPillar;
  twak: TwakPillar;
  nodereal: NodeRealPillar;
  commerce?: CommerceBlock; // optional: older committed snapshots predate it
}

// ── Live on-chain holdings (mirrors WalletOut in schemas.py) ──────────────
export interface WalletAsset {
  symbol: string;
  amount: number;
  usd: number | null;
  price: number | null;
  source: string | null; // "cmc" | "chainlink" | "stable" | null
  is_gas: boolean;
}

export interface Wallet {
  ok: boolean;
  address: string | null;
  explorer_url: string | null;
  block: number | null;
  network: string;
  assets: WalletAsset[];
  total_usd: number | null;
  priced_source: string | null;
  gas_bnb: number | null;
  gas_low: boolean;
  x402_budget_usdc: number | null;
  served_at: string | null;
  note: string | null;
}

// ── CMC Startup-tier market intelligence (mirrors MarketIntelOut) ──────────
export interface GlobalMetrics {
  btc_dominance: number | null;
  eth_dominance: number | null;
  stablecoin_market_cap: number | null;
  total_market_cap: number | null;
  total_volume_24h: number | null;
  altcoin_market_cap: number | null;
}

export interface FngTrendPoint {
  ts: number;
  value: number;
}

export interface Mover {
  symbol: string | null;
  name: string | null;
  pct_24h: number | null;
}

export interface Movers {
  gainers: Mover[];
  losers: Mover[];
}

export interface Category {
  name: string | null;
  avg_price_change: number | null;
  market_cap: number | null;
  market_cap_change: number | null;
  num_tokens: number | null;
}

export interface RegimeTerms {
  breadth: number | null;
  trend: number | null;
  vol_factor: number | null;
  fng: number | null;
  dominance: number | null;
  mktcap: number | null;
  fng_mom: number | null;
  score: number | null;
}

export interface MarketIntel {
  enabled: boolean;
  global_metrics: GlobalMetrics | null;
  fng_trend: FngTrendPoint[];
  movers: Movers;
  categories: Category[];
  regime_terms: RegimeTerms | null;
}

// ── CMC client telemetry (mirrors CmcApiOut) ──────────────────────────────
export interface CmcApi {
  credits_today: number;
  daily_budget: number;
  credits_month: number;
  monthly_budget: number;
  req_count: number;
  last_status: number | null;
  last_credit_count: number;
  rate_wait_total_s: number;
  rpm: number;
  near_cap_day: boolean;
  near_cap_month: boolean;
  healthy: boolean;
  key_set: boolean;
}

export interface AgentHubDerivatives {
  stress: number | null;
  oi_change_24h: number | null;
  funding_rate: number | null;
  open_interest_usd: number | null;
}

export interface AgentHubMacroEvent {
  title: string | null;
  event_date: string | null;
  url: string | null;
  hours_to: number | null;
  high_impact: boolean | null;
}

export interface AgentHubQuote {
  price: number | null;
  pct_24h: number | null;
  pct_7d: number | null;
  volume_24h: number | null;
  market_cap: number | null;
  symbol_cmc: string | null;
}

export interface AgentHubNews {
  title: string | null;
  url: string | null;
  published_at: string | null;
}

export interface AgentHubSkill {
  skill_source: string | null; // "composed" (built on Data MCP) | "cmc-marketplace"
  risk_budget: number | null;
  regime: string | null;
  fear_greed: number | null;
  btc_dominance: number | null;
  mktcap_change_24h: number | null;
  ta_breadth: Record<string, number> | null;
  headline: string | null;
  narratives: string[];
  tools_used: string[];
  derivatives: AgentHubDerivatives | null;
  mktcap_ta: Record<string, number> | null;
  next_macro_event: AgentHubMacroEvent | null;
  quotes_cross_check: Record<string, AgentHubQuote> | null;
  top_news: AgentHubNews[];
}

// Per-token live CMC WebSocket on-chain signals (onchain@* channels). Numeric, symbol-keyed.
export interface OnchainSignal {
  flow_ratio: number | null; // on-chain buy/(buy+sell), 0.5 neutral; >0.5 = net buying
  liquidity_usd: number | null; // total token liquidity (token_agg `lu`)
  top10_pct: number | null; // top-10 holder concentration %
  whale_net_usd: number | null; // net large-swap flow last 1h; <0 = whales net-selling
  net_liquidity_usd: number | null; // net DEX add−remove last 1h; <0 = liquidity leaving
  unique_traders: number | null; // distinct on-chain traders (24h)
  volume_24h: number | null; // CEX 24h volume (cross-reference)
}

export interface AgentHub {
  mcp_enabled: boolean;
  ta_enabled: boolean;
  skill_enabled: boolean;
  x402_enabled: boolean;
  mcp: {
    calls: number;
    by_tool: Record<string, number>;
    tools_available: string[]; // full CMC Data-MCP catalog (12); by_tool holds the exercised subset
    last_call_ts: number | null;
  };
  ta_health: number | null;
  ta_source: string | null;
  skill: AgentHubSkill | null;
  x402: { total: number; settled: number; spent_usdc: number; last_ts: string | null; last_status: string | null };
  onchain_enabled: boolean; // CMC on-chain WebSocket feed active
  onchain: Record<string, OnchainSignal> | null; // {SYM: signal} the agent harvested this tick
  rotation_enabled: boolean; // CMC-native rotation levers on (sector tilt / multi-window momentum)
  rotation: CmcRotation | null; // what the agent rotated toward this tick
}

// CMC-native rotation levers acted on this tick: sector rotation toward trending narratives +
// multi-window CMC momentum. Category names are CMC-native — sanitize with cmcLabel() before display.
export interface CmcRotation {
  trending?: string[]; // live trending_crypto_narratives categories
  sector_hits?: string[]; // held tokens whose CMC sector is trending
  mom?: Record<string, number>; // {SYM: mom_blend of pct_24h/7d/30d}
}

// Result of a LIVE on-demand Agent-Hub probe (/api/agent-hub/ping): a real server-side MCP call
// (tools/list + sample) + a freshly-computed composed Skill. Proves it's not seeded demo data.
export interface AgentHubPing {
  enabled: boolean;
  tools_live: number;
  sample_ok: boolean;
  last_error: string | null;
  ts: string | null;
  skill: { risk_budget: number | null; regime: string | null; headline: string | null; tools_used: string[] } | null;
}

export interface ArmScore {
  arm: string;
  score: number | null;
  dq_safe: boolean;
  forward_eligible: boolean;
  stability: string | null;
  verdict: string | null;
}

export interface Hysteresis {
  consecutive: number | null;
  sustain: number | null;
  margin: number | null;
  incumbent_score: number | null;
  champion_score: number | null;
}

// Forward-gated strategy auto-selector — the recommended LIVE arm + ranked scores (recommend-only).
export interface Evaluation {
  recommended: string | null;
  current: string | null;
  action: string | null; // SWITCH | STAY
  switch: boolean;
  reason: string | null;
  apply_cmd: string | null;
  auto_apply_live: boolean;
  hysteresis: Hysteresis | null;
  scores: ArmScore[];
  ts: string | null;
}

// Scheduler health — last live-tick + dd-watch ages, so a silent cron death is visible on the dashboard.
export interface Scheduler {
  ok: boolean;
  live_tick_age_h: number | null;
  live_tick_stale: boolean;
  dd_watch_age_m: number | null;
  dd_watch_stale: boolean;
  note: string | null;
}

export interface Snapshot {
  health: Health;
  identity: Identity | null;
  strategy: Strategy | null;
  strategies: Strategies | null; // the registry menu + verdicts (dashboard selector)
  live_arm: LiveArm | null; // the REAL live/contest arm + gate (distinct from the SIM lab)
  state: State;
  nav: Nav;
  regime: Regime;
  rebalances: Rebalances;
  rationale: RationaleFeed;
  token_rotation: TokenRotation | null; // per-token touched status (held vs ~0-NAV floor rotation)
  pillars: Pillars | null;
  wallet: Wallet | null;
  market_intel: MarketIntel | null;
  cmc_api: CmcApi | null;
  agent_hub: AgentHub | null; // legacy CMC exhibit (off under free data)
  market_data_hub?: MarketDataHub | null; // live FREE-data-stack exhibit
  economy: EconomyPnL | null; // consolidated agent-economy P&L (one 'true position')
  auto_selector: Evaluation | null; // forward-gated arm auto-selector (recommend-only for live)
  scheduler: Scheduler | null; // scheduler health (last live-tick + dd-watch ages)
  served_at: string | null;
}

/** Consolidated agent-economy P&L — netting trading PnL + commerce revenue − x402 spend into one
 *  'true economic position'. gas is already in trading NAV / heartbeat sponsored (not double-subtracted). */
export interface EconomyPnL {
  trading_pnl_usd: number | null;
  trading_nav_usd: number | null;
  anchor_usd: number | null;
  commerce_revenue_u: number | null;
  commerce_revenue_usd: number | null;
  commerce_self_funded: boolean; // buyer + provider both operator wallets → integration proof, not revenue
  x402_spent_usd: number | null;
  net_economic_usd: number | null; // == trading_pnl_usd (headline = strategy performance only)
  u_valuation_note: string | null;
  gas_note: string | null;
  commerce_note: string | null; // why self-funded commerce is excluded from PnL
  x402_note: string | null; // why x402 data cost is excluded from PnL
}

export interface KillResult {
  ok: boolean;
  engaged: boolean;
  message: string;
}

export interface TokensResult {
  ok: boolean;
  active: string[];
  message: string;
}

// ── Strategy selector (mirrors StrategiesOut / StrategySelectOut in schemas.py) ──
export interface StrategyVerdict {
  passed?: boolean;
  worst_week_dd?: number;
  trades_per_week?: number;
  within_dq_line?: boolean;
  target_dd_met?: boolean;
  forward_eligible?: boolean;
  status?: string;
  median_weekly_ret?: number | null;
  ts?: string;
}

export interface StrategyStability {
  grade: string; // "ROBUST" | "FRAGILE" | "UNSTABLE"
  ts?: string;
}

// Backtest SCOREBOARD over survivors — NOT an edge claim (regime luck, not edge).
// win_rate here is the WINDOW win-rate (share of rolling-7d windows positive), distinct
// from the live DAY win-rate in pnl.ts. Fractions (total_return -0.48 = -48%).
export interface StrategyScoreboard {
  total_return?: number;
  win_rate?: number; // window win-rate (0..1)
  mean_ret?: number;
  median_ret?: number;
  ts?: string;
}

export interface StrategyReadiness {
  state: "ready" | "in_progress" | "not_ready" | "incumbent";
  note?: string;
}

export interface StrategyMenuItem {
  name: string;
  summary: string;
  current: boolean;
  alias_of?: string | null; // underlying arm if this is a BNB_STRATEGY_0X alias
  survival?: StrategyVerdict | null; // backtest gate verdict (the GATE)
  forward?: StrategyVerdict | null; // forward-promotion verdict
  stability?: StrategyStability | null; // robust/fragile/unstable grade (make stability)
  scoreboard?: StrategyScoreboard | null; // backtest perf — SCOREBOARD, not an edge claim
  readiness?: StrategyReadiness | null; // contest-readiness rollup
}

export interface Strategies {
  items: StrategyMenuItem[];
  current: string;
}

export interface LiveArm {
  name: string;
  source: string; // "live journal" | "configured (STRATEGY_NAME)" | "default"
  candle_source?: string | null;
  survival?: StrategyVerdict | null; // the GATE (DQ rail)
  forward?: StrategyVerdict | null; // forward-promotion verdict
  switchable?: boolean;
  note?: string | null;
}

export interface StrategySelectResult {
  ok: boolean;
  strategy: string;
  available: string[];
  message: string;
}
