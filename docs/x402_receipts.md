# x402 Receipts — the agent pays CoinMarketCap per request (TWAK-special artifact)

> Native x402 usage: the agent buys CMC Agent Hub data **per call** ($0.01 USDC on Base,
> chain `eip155:8453`) instead of holding an API key — every settled call is an on-chain
> USDC micropayment. This document explains the flow, the safety rails, and indexes the real
> receipts in [`data/x402/receipts.json`](../data/x402/receipts.json). Implementation:
> [`src/ictbot/data/x402_cmc.py`](../src/ictbot/data/x402_cmc.py).

## 1. What this is

CMC's `pro-api.coinmarketcap.com/x402/*` endpoints support **x402** — the HTTP-402
pay-per-request protocol. Instead of an `X-CMC_PRO_API_KEY`, the agent answers a 402
challenge by signing a USDC payment and resending. No account, no subscription, no key — the
agent funds its own data feed. The payment wallet is the **ERC-8004 identity wallet**
`0xEb7bF36aab4912c955474206EF0b835170389655` (the same address that holds agentId 133085 and
heartbeats) — **not** the TWAK trading wallet. Off by default (`X402_ENABLED`).

## 2. The payment flow (402 → sign → resend)

```
GET  /x402/v1/dex/search?q=bnb
  → 402 Payment Required + an x402 V2 challenge (accepts[] payment options + resource)
  → pick the Base-USDC EIP-3009 accept
  → sign a USDC TransferWithAuthorization with bnbagent's X402Signer (EIP-712, EIP-3009)
  → resend with the PAYMENT-SIGNATURE header (base64 payload that ALSO echoes the chosen
     `accepted` option + the `resource`)
  → 200 + the data; the payment settles on-chain (USDC leaves the identity wallet)
```

**Two format details confirmed live against CMC's facilitator (2026-06-12)** — both were
required to get a 200, and both differ from the bare x402 spec:
- The resend header is **`PAYMENT-SIGNATURE`** (CMC's published name). The spec's `X-PAYMENT`
  is silently ignored and the request is re-challenged.
- The V2 payload must **echo the chosen `accepted` option and the `resource`** from the
  challenge. Omitting them returns `"Missing accepted in PAYMENT-SIGNATURE payload (x402 V2)"`
  / `"payment header resource is null"`.

## 3. Safety rails (a malicious 402 cannot redirect or inflate the payment)

Layered on top of the wallet's own `SigningPolicy` (see `x402_cmc.py`):
- **Domain allowlist** (`_signing_policy`) — extends the SDK's strict default to allow signing
  *only* the Base-USDC (chainId 8453, `0x8335…2913`) EIP-3009 domain; every other guard
  (EIP-3009-only primary type, Permit denylist, validity window) is kept.
- **Per-call cap** `X402_MAX_VALUE_PER_CALL_UNITS` = 10000 units = **$0.01**, and a
  **session budget** `X402_SESSION_BUDGET_UNITS` = 1,000,000 units = **$1.00** per process
  (`X402Signer`).
- **Recipient match** — the signer is bound to the `payTo` the caller committed to; a 402 that
  swaps in a different recipient is refused.
- **Validity window** — `validAfter` is backdated 60s for clock skew and `validBefore` bounded
  (`_MAX_VALID_BEFORE`) so the authorization window stays under the SDK's 600s cap.
- **Off by default + graceful fallback** — `available()` is false unless `X402_ENABLED` + the
  SDK + a wallet password are all present; on **any** failure the caller falls back to the
  keyed pro-api in [`cmc.py`](../src/ictbot/data/cmc.py). Every attempt (settled or failed)
  appends a receipt.

## 4. Payable endpoints used

| Path | What it buys |
|---|---|
| `/x402/v1/dex/search` | CMC AI Agent Hub DEX search (the per-tick pillar-1 enrichment) |
| `/x402/v3/cryptocurrency/quotes/latest` | Pay-per-call market quotes (real prices paid on-chain) |

## 5. Receipt schema

Each row in `data/x402/receipts.json` (`_log_receipt`):

```json
{ "ts": "2026-06-12T06:16:48Z", "endpoint": "/x402/v1/dex/search", "status": "settled",
  "payTo": "0x3C5f3a6cE224BB89D72f5EB4232ecC27F67B3eeA",
  "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "value": 10000, "network": "eip155:8453" }
```

`value` is in USDC 6-dp units (10000 = $0.01). `status` is `settled` or `failed` (failures
record an `error` class instead of payment fields).

## 6. The receipts (real, settled on Base)

Generated from the funded identity wallet; the live count grows as the loop runs (source of
truth: `data/x402/receipts.json`). The receipts file proves the settled calls below; separately, the
payer's on-chain USDC balance is observable falling by $0.01/call. Verify the balance via the payer's
Base USDC transfer history (§7).

| Metric | Value (live — count grows with every dashboard refresh) |
|---|---|
| Total receipts | **30+ settled, and counting** (exact live count in `data/x402/receipts.json`) |
| Settled | **100% settled** (all on-chain) |
| Cost | **$0.01 USDC / call** on Base |
| Endpoints | `/x402/v1/dex/search` (most) · `/x402/v3/cryptocurrency/quotes/latest` |
| First receipt | 2026-06-12T06:16:48Z (latest = newest row in `data/x402/receipts.json`) |
| Asset / network (all) | USDC `0x8335…2913` / Base (`eip155:8453`) |
| Recipient (all) | `0x3C5f3a6cE224BB89D72f5EB4232ecC27F67B3eeA` (CMC) |

*This table is derived from `data/x402/receipts.json` by `reads._x402_receipts()` — the same
summary the dashboard renders; regenerate it from the file rather than editing by hand.*

## 7. Verify it independently

- **Live dashboard** — the CMC Agent Hub panel
  ([CmcAgentHubPanel.tsx](../web/src/components/CmcAgentHubPanel.tsx)) shows the settled
  count + USDC spent; the API surfaces it at
  [`/api/pillars`](https://mission-control-api-iez5.onrender.com/api/pillars) (`cmc.receipts`)
  and `/api/snapshot` (`agent_hub.x402`).
- **On-chain** — the payer is the identity wallet
  [`0xEb7b…9655`](https://basescan.org/address/0xEb7bF36aab4912c955474206EF0b835170389655);
  its USDC transfers on Base are the settled payments.

## 8. Reproduce

```bash
# .env: X402_ENABLED=true  (+ AGENT_WALLET_PASSWORD set, identity wallet funded with USDC on Base)
make run_allocator        # a tick fires one dex_search x402 read; each settled call appends a receipt
```

The only code path that *pays* is `scripts/run_allocator.py` (the allocator tick). The
dashboard read path only **reads** the receipts file and the public USDC balance — it never
pays. The test suite is guarded (`tests/conftest.py`) so `make test` can never settle USDC.
