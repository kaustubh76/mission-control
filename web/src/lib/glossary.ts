// Plain-English explanations for the trading / on-chain jargon surfaced in the UI.
// Consumed by the <InfoTip term="…" /> primitive so judges and newcomers can decode
// every term in place, while the precise number stays visible next to it.

export interface GlossaryEntry {
  /** The proper term, shown bold at the top of the tooltip. */
  term: string;
  /** One-sentence plain-English explanation. */
  plain: string;
}

export const GLOSSARY = {
  nav: { term: "NAV", plain: "Net Asset Value — the total dollar value of everything the agent holds right now." },
  hwm: { term: "High-Water Mark", plain: "The highest NAV ever reached. Drawdown is measured down from here, not from the start." },
  drawdown: { term: "Drawdown", plain: "How far NAV has fallen below its all-time high. The agent's #1 risk metric." },
  dqLine: { term: "DQ Line (30%)", plain: "Hard risk line: lose 30% from the high and you're out. The agent never goes near it." },
  teamCap: { term: "Team Cap (15%)", plain: "Our own stricter stop — half the DQ limit — so we de-risk long before the line." },
  regime: { term: "Market Regime", plain: "Whether the market is risk-on (calm / rising) or risk-off (fearful / falling)." },
  riskOnScore: { term: "Risk-On Score", plain: "A 0–1 reading of market health. Higher means the agent is allowed to deploy more capital." },
  deployCap: { term: "Deploy Cap", plain: "The max share of capital allowed into risky tokens right now; the rest sits in stablecoins." },
  deployBand: { term: "Deploy Band", plain: "The agent scales its deploy cap within this band as the live regime score moves — risk-on deploys more, risk-off pulls to cash. The cap is adaptive, not a fixed number." },
  fearGreed: { term: "Fear & Greed", plain: "The 0 (extreme fear) to 100 (extreme greed) crypto market-mood index, read live from alternative.me." },
  deployed: { term: "Deployed", plain: "Share of the book actually held in tokens, versus parked in USDT stablecoin." },
  paperBook: { term: "Trading Book", plain: "The agent's on-chain strategy ledger — its tracked NAV (USDT + token holdings) marked to market after each rebalance. The Wallet card shows the raw on-chain balance." },
  realFunds: { term: "Real Funds", plain: "Live, on-chain wallet balance read straight from the chain — actual money." },
  twak: { term: "TWAK", plain: "Trust Wallet API Kit — how the agent self-custodies keys and signs real on-chain swaps." },
  x402: { term: "x402", plain: "A pay-per-call protocol: the agent pays tiny USDC amounts for premium data, on demand." },
  mcp: { term: "MCP", plain: "Model Context Protocol — the open standard the agent uses to pull live market data." },
  cmcPipeline: { term: "Data Pipeline", plain: "Every decision input comes from free, keyless market data: Binance klines + alternative.me Fear & Greed + DexScreener DEX signals + CoinGecko globals → the composed market-overview → the agent's allocation." },
  candleSource: { term: "Candle Source", plain: "Which price candles the ranking ran on. The live arm ranks on free 4h candles from Binance public klines (data-api.binance.vision)." },
  skills: { term: "Composed market-overview", plain: "A regime read the agent builds by fusing several free market-data sources (Fear & Greed, breadth, DEX signals, macro globals) into one risk-budget signal." },
  erc8004: { term: "ERC-8004", plain: "An on-chain agent-identity standard. Proves this exact agent acted, verifiable on-chain." },
  webproof: { term: "Web Proof (zkTLS)", plain: "A vlayer cryptographic proof that a specific HTTPS response really came from a given server (here alternative.me's Fear & Greed endpoint) — verifiable on-chain, without revealing anything else. Turns the sold report's data provenance from a claim into proof." },
  gasless: { term: "Gasless", plain: "Identity + heartbeat transactions are sponsored (MegaFuel), so the agent pays no gas for them." },
  ta: { term: "Technical Analysis (TA)", plain: "Momentum / breadth signals the agent layers on top of regime to tune position sizing." },
  rebalance: { term: "Rebalance", plain: "A daily reshuffle — sell some, buy others — to track the shifting momentum leaders." },
  heartbeat: { term: "Heartbeat", plain: "A periodic on-chain liveness ping proving the agent is alive and running." },
  killSwitch: { term: "Kill Switch", plain: "An emergency halt that stops evaluations and flattens positions to cash." },
  credits: { term: "API Credits", plain: "Metered usage units for any keyed data source. The agent stays inside a daily / monthly budget." },
  btcDominance: { term: "BTC Dominance", plain: "Bitcoin's share of total crypto market cap — a quick risk-on / risk-off tell." },
  movers: { term: "Top Movers (24h)", plain: "Biggest 24h gainers and losers among the top ~100 coins by market cap — the broad market's real movers, not micro-cap pumps." },
  activeTokens: { term: "Active Tokens", plain: "The subset of the 8-token universe the agent may rank and buy. Toggling one off sells it on the next rebalance — it never strands a position." },
  diagnostics: { term: "System Diagnostics", plain: "Live read-only probes that confirm each subsystem — API, wallet, market data, regime, identity — is actually responding right now. They never trade or change state." },
  winRate: { term: "Win Rate", plain: "Share of trading days the live book finished up (flat days excluded). Not per-trade — the agent holds a rebalanced portfolio, so this counts up-days, reconcilable with the green bars." },
  gate: { term: "Survival Gate", plain: "The hard pass/fail test an arm must clear to be ranked at all: worst rolling-7-day drawdown under 25% AND at least 7 trades/week. Risk-first — a gate, not a performance score." },
  scoreboard: { term: "Scoreboard (not an edge claim)", plain: "Backtest PnL & win-rate over the arms that already cleared the survival gate. There's no proven long-only edge on this universe, so these reflect how much an arm rode a trending sample — regime luck, not a repeatable edge." },
  backtestReturn: { term: "Backtest total return", plain: "An arm's cumulative return over the long backtest sample. Scoreboard only — dominated by how much it rode the trend, never presented as edge." },
  windowWinRate: { term: "Win-rate (window)", plain: "Share of rolling 7-day backtest windows that finished positive — a robustness read, distinct from the live day win-rate on the P&L card." },
  stabilityGrade: { term: "Stability Grade", plain: "How trustworthy an arm's verdict is across data windows, frictions and regimes: ROBUST (holds up), FRAGILE (verdict wobbles), UNSTABLE (don't trust it)." },
  forwardCheck: { term: "Forward Check", plain: "Live forward-track promotion test — worst-7d DD, trades/week and median weekly return on real forward days. Prefers an arm's isolated track; 'accruing' until enough days resolve." },
  readiness: { term: "Strategy Readiness", plain: "A rollup of survival + stability + forward. READY = all automated gates cleared (human sign-off still required); IN PROGRESS = forward still accruing; NOT READY = a gate failed; INCUMBENT = the locked live allocator." },
} as const;

export type GlossaryKey = keyof typeof GLOSSARY;
