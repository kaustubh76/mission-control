# BNB Mission Control — DoraHacks submission

> Paste-ready submission text (~500 words) + the links block. Track 1 (Autonomous Trading
> Agents), stacking all three pillars; also entered for the partner awards (Best Use of
> CoinMarketCap / Trust Wallet Agent Kit / BNB Chain).

---

**An autonomous, self-custody trading agent on BSC that doesn't just trade — it runs a
two-sided agent economy on one on-chain identity: it *buys* its own CoinMarketCap data with
native x402 micropayments and *sells* its market analysis to other agents via ERC-8183, while
Trust Wallet Agent Kit is the only key that ever signs.** All three sponsor pillars are
load-bearing, not decorative.

**① CoinMarketCap is the eyes.** A live regime score (basket breadth + trend + volatility + CMC
Fear & Greed) drives every allocation; the Agent Hub **MCP** supplies pre-computed TA/macro that
A/B-testing showed *reduces drawdown* (the `enhanced+ta` config is the best arm); the agent
streams CMC's own 4h candles over WebSocket; and it pays for data via **native x402** — **49 real
USDC micropayments settled on Base** ($0.01/call, and counting — every refresh buys fresh data), **no API key**.
**② Trust Wallet Agent Kit is the only thing that signs.** Every swap, the `twak compete`
registration, even the gas top-up went through the local `twak` CLI — keys never leave
`~/.twak/wallet.json`, no cosigner, no custodial step. **③ The BNB AI Agent SDK** gives it an
on-chain **ERC-8004 identity** (agentId 133085) that heartbeats its NAV + plain-language
rationale on-chain each tick it runs (mint + first heartbeat verified on-chain), and runs the
SDK's flagship **ERC-8183** agentic-commerce layer.

**The standout — a working agent-to-agent economy.** The same identity wallet
(`0xEb7b…9655`, distinct by design from the TWAK trading wallet) both **buys** data (x402) and
**monetizes** its analysis: it sells its live CMC Regime Report to a peer agent for an on-chain
fee. **Two real jobs served end-to-end on BSC mainnet** — `create → fund → submit signed
deliverable on-chain → IPFS-pinned report` — to a genuine agent buyer
(`0x9e4A…74d6`): job **25741** (tx `0x73546c6d…`, `ipfs://QmTXDHUPYTTFbqptJvjAsNAVPnCfaNVso9CmHpjYBb5cnp`)
and job **26506** (tx `0xcc13c3e6…`, `ipfs://Qmd6hqiF4QRnLEnw282SmACc5RYwbSBBDn4xdHzZojFRoY`),
settlement finalizing after the on-chain dispute window. One agent that funds its own inputs and
earns from its own outputs.

**The strategy is risk-engineered, not hype.** The live arm is a **regime-adaptive allocator**
running mean-reversion: each ≈daily rebalance it buys oversold names (>1σ below their rolling
mean), inverse-vol-weighted, and deploys *adaptively* — the risk-on score scales the deployed
fraction inside a **[0.40, 0.85]** band, so it leans in when the week trends and defends to cash
when it doesn't. Risk is the adaptive cap plus a **hard drawdown halt** against the high-water
mark. For Track 1 this is **controlled long beta under a hard drawdown halt vs the 30% DQ line** —
good weeks compound, bad weeks are capped. We earned that posture honestly: we audited this
universe for a long-only edge five independent ways, found only variance around break-even, and so
engineered for the actual scoring function — **survival, participation, and risk-adjusted
consistency** — rather than pretending alpha exists (survival-passed: worst-week drawdown **13.2%**
vs the 30% DQ line, **~26 trades/wk** vs the 7-trade floor), then forward-validate in paper daily
on unseen data.

The public Mission Control dashboard is a forward paper track with **dated freshness cues** and
carries **no funds-bearing secret** (only a low-value ingest token gating its refresh write) — a
cloud compromise can at worst spoof public dashboard data, never move funds. The agent performs
spot swaps only — no token launches, fundraising, or airdrops during the event window.

**Built and proven at real scale — by the numbers (2026-06-21).** 35,304 CMC 4h candles processed
across 8 tokens over 7 live WebSocket channels (the same harvest already targets the 149-token BSC
universe) · 2,338 rolling 7-day backtest windows · 49 x402 micropayments settled on Base · 2 ERC-8183
jobs on BSC mainnet · 20 strategy arms (10 on live forward paper tracks) · ~38k lines of Python with
1,600+ tests · 112 MB journaled market data. **The volume of data already flowing is the scalability
proof:** adding tokens or strategy arms is config, not re-architecting ([SCALABILITY.md](SCALABILITY.md)).

## Links

- **Repo:** <this repository>
- **Dashboard (Mission Control):** https://bnb-mission-control-two.vercel.app
- **Read-only API:** https://bnb-mission-control-api.onrender.com (`/api/health`, `/api/pillars`)
- **Agent / participant address:** `0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215` (`isRegistered=true` on `0x212c61b9b72c95d95bf29cf032f5e5635629aed5`)
- **ERC-8004 identity:** agentId 133085 — https://bscscan.com/token/0x8004A169FB4a3325136EB29fA0ceB6D2e539a432?a=133085
- **ERC-8183 agent commerce (BSC mainnet):** jobs 25741 + 26506 — see [docs/erc8183_agent_commerce.md](docs/erc8183_agent_commerce.md) (`data/journal/commerce_jobs.jsonl`)
- **x402 receipts:** [docs/x402_receipts.md](docs/x402_receipts.md) (49 settled on Base, and counting; see `data/x402/receipts.json`)
- **Demo video:** `<TBD: demo video URL — record per DEMO.md>`
- **DoraHacks BUIDL:** `<TBD: BUIDL URL>`
- **Sample live swap tx (TWAK-signed, BSC):** [`0x9d64…67d1`](https://bscscan.com/tx/0x9d64945b28ce5f217471299599bb30406ac5a9f7a6fb873c917aa697aa5867d1) (USDT→CAKE) + [`0xf08f…0380`](https://bscscan.com/tx/0xf08f1b4f0b7d00a23ff7255f6da70270dbfba389b5f19d182dd055ec6a5c0380) (CAKE→USDT) — a pre-window proof round-trip; in-window swaps follow during the trading week

---
*Word count ~580 incl. the by-the-numbers line; trim that line (or the hook) if the form caps tighter.
The brief's §12 "Prometheus metrics screenshot" deliverable is superseded by the live Mission Control
dashboard URL above.*
</content>
