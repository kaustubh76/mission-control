# DEMO.md — 3:30–4:30 demo video script (agent-economy first)

> Recording script for the submission video. The **lead story is the agent economy**: the agent
> *sells* its market read to other agents via ERC-8183 — one self-custody identity, all on-chain — and
> in the original BNB-hackathon build also *bought* its data via x402 micropayments. The regime-adaptive
> **mean-reversion** trade loop is shown as what that economy funds. VO lines are verbatim; shot list +
> live-vs-prerecord per segment. Target 3:30–4:30.
>
> **Update (2026-07): the agent now runs on FREE, keyless data** (Binance · alternative.me ·
> DexScreener · CoinGecko). The live dashboard shows the **Market Data Hub**, not the old CMC Agent
> Hub — film that panel. x402 / CoinMarketCap appear only as *narrated hackathon history* (the 49 Base
> receipts are still real artifacts to show); don't present them as the current live feed.

## Pre-roll setup (have open before recording)

- The live dashboard: <https://bnb-mission-control-two.vercel.app> (warm it first — load once so Render isn't cold).
- A terminal in the repo (venv active), `.env` with `ENABLE_LIVE_TRADING=true`, `FREE_DATA=true` (the
  default — runs keyless; leave `X402_ENABLED` off unless filming the historical x402 receipts beat).
- Tabs ready: **BscScan** (for the TWAK swap + the ERC-8183 submit tx + identity 133085), a **Base explorer**
  (for the x402 payer), and an **IPFS gateway** (for the ERC-8183 deliverable CID).
- `twak` CLI authed (`~/.twak/wallet.json`, `TWAK_WALLET_PASSWORD` set).
- Pre-record any network-dependent shot (live swap, RPC reads) as a fallback so a flaky node can't break a take.

---

## Segment script

**[0:00–0:30] Hook = the economy.** *Shot:* dashboard hero → pan to the **Market Data Hub** panel and the
**Agent Commerce** panel side by side.
VO: "Most trading agents just trade. This one runs an *economy*. It's fully self-custody — it reads the
market from free, public data and **sells** its market read to other agents, autonomously, on-chain. One
identity that earns from its outputs. Let me show you — live."

**[0:30–1:10] The data layer — live free Market Data Hub.** *Shot:* the **Market Data Hub** panel —
regime, Fear & Greed, BTC dominance, and the per-token DexScreener signal table updating live.
VO: "Everything the agent decides on comes from free, keyless public data — Binance candles,
alternative.me Fear & Greed, DexScreener liquidity, CoinGecko macro. No API key, no subscription."
*(Optional history beat — Shot:* `data/x402/receipts.json` + a **Base explorer** tx for payer
`0xEb7b…9655`.) VO: "In the original hackathon build it even *paid* for its data over x402 —
**49 USDC micropayments, still on Base** — before moving to the free stack."

**[1:10–2:05] Sell side — ERC-8183 (BNB AI Agent SDK / pillar 3). ⭐ the standout — give it the most time.**
*Shot:* `data/journal/commerce_jobs.jsonl` showing jobs **25741** and **26506** (`create → fund → submit →
on-chain`), then **BscScan** for submit tx `0x73546c6d…`, then the **IPFS** deliverable
`ipfs://QmTXDHUPYTTFbqptJvjAsNAVPnCfaNVso9CmHpjYBb5cnp` opened in a gateway.
VO: "The agent packages its Market Regime Report and **sells it to a peer agent** for an on-chain fee —
create the job, fund it, submit a signed deliverable on-chain, pin the report to IPFS. Here are two real
jobs served end-to-end on **BNB Smart Chain mainnet**, to a genuine agent buyer. This is a real
agent-to-agent economy — not a slide. It earns with ERC-8183 under **one** identity wallet."

**[2:05–2:55] What the economy funds — the trade loop (free data → regime → TWAK / pillar 2).** *Shot:*
`make run_allocator` — highlight the printed `regime`, `cap`, `F&G`, weights; then a TWAK-signed swap
(`make run_allocator ARGS="--mode live"`, pre-recorded) → the tx hash → open it on BscScan.
VO: "All of that funds the trading. One tick: the agent reads live Fear & Greed from alternative.me and
Binance candles, folds them into a regime score that sets an adaptive deployment cap between 40 and 85 percent,
and the live **mean-reversion** arm buys oversold names, inverse-vol sized. **Trust Wallet Agent Kit is the
only signer** — the swap signs locally, no key ever leaves the wallet — and returns a transaction hash on BSC."

**[2:55–3:25] Why it can't blow up (guardrails + identity).** *Shot:* dashboard RegimeDial + RebalanceTable +
trades-toward-7 counter; then BscScan token page for **agentId 133085**.
VO: "Two contest gates, each double-defended: an adaptive cap **and** a hard drawdown halt against the
high-water mark for the 30% line, plus a trade-floor nudge for the 7-trade minimum. State writes are atomic;
failed swaps are contained. And every decision is written on-chain as an **ERC-8004 heartbeat** under identity
133085 — its reasoning, on the record."

**[3:25–3:55] Close.** *Shot:* the three-pillar status panel; end card with links.
VO: "Self-custody, autonomous, **agent-monetized** — reads free public market data, signed by Trust
Wallet, identified and trading on BNB Chain. Repo and live dashboard are in the description."

---

## Bonus appendix — the switching engine actually switches (optional, if you want a 5th min)

The dashboard's **Auto-selector** usually reads **STAY** by design — it refuses to switch the live arm to
anything that hasn't proven itself on an *unseen, deployed* forward track, then holds for 2 consecutive evals
(anti-chasing). The harness proves it fires, in a throwaway isolated data dir (live `data/` + `.env` untouched):

```bash
bash scripts/demo_auto_selector.sh
# round-1 STAY (won't chase a one-off) → round-2 SWITCH (sustained edge → fires) → round-3 live-apply (sim)
```
VO: "The switching engine isn't inert — a challenger that proves out on an unseen forward track fires a real
switch; live stays recommend-only until operator sign-off." Exits non-zero if the switch fails, so it doubles
as a smoke test.

## Appendix — commands + live-vs-prerecord

| Segment | Command / action | Live or pre-record |
|---|---|---|
| Data layer (free) | dashboard **Market Data Hub** panel (regime · F&G · BTC dom · DEX table) | live |
| History beat (x402) | `data/x402/receipts.json` + Base explorer tx for `0xEb7b…9655` | live (reads existing 49 receipts) |
| Sell side (ERC-8183) | `commerce_jobs.jsonl` + BscScan submit tx `0x73546c6d…` + IPFS `QmTXD…` | live (on-chain proof already exists) |
| Free-data read + regime | `make run_allocator` | live (sim — safe, no spend) |
| TWAK swap | `make run_allocator ARGS="--mode live"` | **pre-record** (needs live BNB + clean RPC); banks the real tx hash |
| Identity / heartbeat | BscScan token 133085 | live |
| Dashboard | <https://bnb-mission-control-two.vercel.app> | live (warm it first) |
| Auto-selector (bonus) | `bash scripts/demo_auto_selector.sh` | live (isolated tmp — safe) |

**Not shown (superseded):** there is no ICT signal, no SL/TP bracket, and no `:9100` Prometheus scrape — the
read-only dashboard is the telemetry surface.

**Before recording (user):** record to this script, then fill the demo URL in [README.md](README.md) §1 +
[SUBMISSION.md](SUBMISSION.md). The live swap tx and the x402-on tick are both capturable on camera.
</content>
