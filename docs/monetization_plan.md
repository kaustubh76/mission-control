# Monetization Plan — "Trade with the Agent" (Portfolio-as-a-Service)

> **Vision:** turn the live, risk-controlled trading agent into a product — showcase its on-chain
> PnL track record, let other people put it to work on their own portfolios, and take a fee that
> flows to the agent wallet on every trade it drives.
>
> **Status: CAPTURED IDEATION — a brainstorm, NOT a greenlit build.** This began as the founder
> thinking out loud about monetization; it's written down here as a reference design to return to
> later. **Nothing here should be auto-executed.** Active building (if/when it happens) continues on
> a separate track/chat and needs an explicit go-ahead first. Kept security-first, testnet-first, and
> audit-gated for any path that would touch other people's funds.

**Decisions locked (with the user):**
- **Architecture = phased.** Phase 1 ships the **non-custodial fee-on-swap** product (safe, fast,
  demoable, no custody). Phase 2 adds the **managed ERC-4626 vault** (passive "invest & forget"),
  gated behind a professional audit.
- **Fee = hybrid.** A **small per-swap builder fee** (honors "a fee on every trade") **+ a 5%
  performance fee** on profits (high-water mark) — the performance fee is enforceable only in the
  custodial vault (Phase 2); Phase 1 runs on the builder fee.

---

## 0. The one hard constraint (read this first)

You can only take a cut of "any trade by any other wallet" **if that trade flows through a
contract/router you control.** There is no hook to skim a fee from a swap a user does directly
from their own wallet on PancakeSwap. So monetization MUST route trades through either:
- **(Phase 1)** our dapp's swap call → a **DEX aggregator** that supports an **affiliate/builder
  fee recipient** (the fee is taken atomically by the aggregator and sent to our treasury), or
- **(Phase 2)** a **vault contract** that executes the swaps and deducts the fee.

**Separation from the contest:** this fee/vault path is **independent of the contest's TWAK
execution** (the contest only scores TWAK swaps from the agent's own wallet). The reusable asset
is the agent's **brain** — the CMC→regime→target-weights engine + the NL rationale + the PnL track
record. The "hands" differ: contest = TWAK (own wallet); product = aggregator/vault (others' money).

---

## 1. Business model

```
   Agent's live, on-chain PnL track record  (the proof — already journaled + dashboarded)
            │  attracts users
            ▼
   "Trade with the Agent"  ──────────────────────────────────────────────┐
     Phase 1: non-custodial — users mirror the agent's allocation via     │  fee on every
              our dapp; aggregator routes a builder fee to the treasury    │  trade the agent
     Phase 2: custodial vault — users deposit; agent auto-manages;         │  drives  ─────────▶  Agent treasury
              builder fee + 5% performance fee on profits                  │                      (multisig)
   ──────────────────────────────────────────────────────────────────────┘
```

- **Acquisition:** the existing Mission Control dashboard becomes a public **PnL showcase /
  leaderboard** (verifiable on-chain track record, regime-adaptive risk control, NL rationale —
  "here's exactly why it traded"). Trust is the product.
- **Revenue:** builder fee per trade (both phases) + 5% performance fee (Phase 2 vault).
- **Moat:** transparency + risk control (DQ-proof discipline) + the agent's explainability, not a
  claim of alpha (see §9 — we do **not** promise alpha; we promise disciplined, transparent,
  risk-controlled management).

---

## 2. Fee model (hybrid) — exact mechanics

| Component | Rate (recommended) | Base | When taken | Phase | Goes to |
|---|---|---|---|---|---|
| **Builder fee** | **0.5%** (configurable; cap-verified per aggregator) | swap notional (the buy-side amount) | atomically, on every swap routed through us | 1 + 2 | agent treasury wallet |
| **Performance fee** | **5%** | profit **above the user's high-water mark** | crystallized on withdraw / epoch | **2 only** (needs custody to measure & collect) | agent treasury wallet |
| ~~Management/AUM fee~~ | — | — | — | optional later | — |

**Why not literal "5% per trade":** 5% × turnover × ~daily rebalancing would dwarf returns and make
the product uncompetitive (typical aggregator affiliate fees are 0.1–1%). The hybrid honors "a fee
on every trade" with a small, competitive per-swap fee, and earns the real upside on **results** via
the performance fee — which only charges when the user is in profit (aligned incentives).

**Worked example (Phase 2 vault, user deposits $10k):**
- Agent rebalances → swaps $4k of notional that day → builder fee = 0.5% × $4k = **$20** → treasury.
- Over a quarter the user grows $10k → $11k (new high-water mark) → performance fee = 5% × $1k profit
  = **$50** → treasury (charged on crystallization; never on the principal, never below the HWM).

**Note (verify, don't guess):** each aggregator caps the affiliate fee differently (e.g. 0x's
`buyTokenPercentageFee`, 1inch's `fee`/referrer). **Confirm the max allowed bps + the exact param
name against the chosen aggregator's live API before wiring** — same no-guess discipline used for
the `twak` flags.

---

## 3. Phase 1 — Non-custodial "Trade with the Agent" (ship first)

**Principle: we never touch user funds.** Users keep full custody; they execute their own swaps
through our dapp, which mirrors the agent's live target allocation and routes a builder fee to the
treasury via the aggregator.

### Flow
```
1. User connects wallet (wagmi/RainbowKit) on our dapp.
2. Dapp shows: the agent's LIVE target weights + its PnL track record + per-decision NL rationale.
3. Dapp computes the user's deltas (their current balances vs the agent's target).
4. User clicks "Rebalance with the Agent" → for each delta, the dapp requests a quote from the
   aggregator with feeRecipient = treasury, feeBps = builder fee.
5. User signs each swap from THEIR wallet (or one batched tx) → aggregator executes + routes the fee.
   → Funds never leave the user's custody except into the tokens they chose; the fee is atomic.
```

### Components
- **Allocation feed:** a read-only endpoint exposing the agent's current target weights + rationale
  (reuse the existing `api/` snapshot — `state.weights` / `regime` / `rationale`). No new strategy code.
- **Swap widget (FE):** wallet connect, quote, approve (Permit2 where supported to avoid blanket
  allowances), execute. New React surface alongside the existing dashboard.
- **Aggregator adapter:** 0x or 1inch Swap API on BSC (both support an affiliate fee recipient).
  Pick one after verifying the fee param + cap.
- **(Optional, better UX) auto-follow:** session keys / a delegated signer (e.g. a smart-account /
  ERC-4337 or a scoped session key) so the user pre-authorizes the dapp to execute *bounded* rebalances
  (token allowlist + max size + expiry) without clicking each trade — still non-custodial (no withdraw).

### Security (Phase 1 — low risk by design)
- **No custody contract** → no honeypot, no "drain the vault" class of bug.
- **Approvals hygiene:** prefer **Permit2 / exact-amount approvals**; never request unlimited
  approvals to an unaudited contract. The aggregator's router is the (already-audited) approval target.
- **Slippage protection** (minReceived) on every quote; show the user the fee + price impact explicitly.
- **Fee transparency:** the builder fee is shown pre-trade and is on-chain verifiable.
- **Session-key path (if built):** scope = token allowlist + per-tx max notional + expiry + **no
  transfer/withdraw capability**; revocable anytime.

### UX
- A **PnL showcase / leaderboard** (the agent's verifiable on-chain record), "your portfolio vs the
  agent" diff, one-click rebalance, clear fee + slippage disclosure, the NL "why I'd trade this" panel.

### Phase 1 milestones
1. Allocation feed endpoint (reuse snapshot). 2. Aggregator adapter + fee config (verify cap). 3. Swap
widget FE + wallet connect. 4. PnL showcase page. 5. Testnet end-to-end. 6. (optional) session-key auto-follow.

---

## 4. Phase 2 — Managed Vault (ERC-4626) (after audit)

The passive "invest & forget" product. Users deposit; the agent auto-manages; fees are taken on-chain.

### Architecture
```
User ─deposit USDT─▶ [ AgentVault : ERC-4626 ] ─mints shares─▶ User
                         │  assets held by the vault (pooled)
                         │  KEEPER role = the Python agent (web3), TRADE-ONLY:
                         │     • rebalance(targets) → swaps via an ALLOWLISTED router
                         │       among an ALLOWLISTED token set, with slippage + size caps
                         │     • CANNOT transfer assets to any non-vault address (no withdraw)
                         │  builder fee (0.5%) deducted per swap → treasury
                         │  performance fee (5% over HWM) crystallized on withdraw/epoch → treasury
                         └─ User withdraws pro-rata ANYTIME (even if keeper is paused) → emergency exit
```

### The security model (the heart of Phase 2 — real funds)
- **Role separation:** `owner` (multisig, param changes behind a **timelock**) · `keeper` (the agent;
  can ONLY call `rebalance` within constraints) · `users` (deposit/withdraw their own shares).
- **Keeper is trade-only:** it can swap among an **allowlisted token set** via an **allowlisted
  router** only. It can **never** move assets to an external address. ⇒ a compromised keeper key
  **cannot drain** the vault — worst case it churns within bounds (capped by the below).
- **Per-trade caps:** mandatory `minReceived` slippage bound, max-trade-notional, and a rebalance
  **cooldown** — so even adversarial churn is bounded and cheap-to-detect.
- **Users always exit:** pro-rata `withdraw`/`redeem` works even when the keeper is **paused**
  (emergency withdrawal is never gated by the keeper).
- **Standard hardening:** OpenZeppelin `ERC4626` + `ReentrancyGuard` + `Pausable` + `AccessControl`;
  no naive upgradeability (either non-upgradeable, or a timelocked, audited proxy); deposit **cap**
  (start small, ramp); high-water-mark accounting per share so the perf fee never charges principal.
- **Treasury:** fees land in a **multisig** treasury (not a hot key). Keeper key + treasury key are
  separate and hardware/multisig-protected.
- **Process:** Foundry unit + **fuzz/invariant tests** (e.g. "share value monotonic ex-fees",
  "keeper can't reduce total assets beyond slippage caps", "sum of withdrawable ≤ total assets") →
  internal red-team → **professional audit** → mainnet with a low deposit cap → ramp.

### Keeper = the existing agent
The Python runtime becomes the vault **keeper** via a new web3 broker (a `VaultKeeperBroker` that
calls `vault.rebalance(targets)` instead of TWAK). The **strategy/regime engine is reused verbatim**;
only the execution adapter is new. All the hardening from `docs/build_audit.md` (atomic state,
idempotency lock, DD halt, preflight, reaction-time fast monitor) applies to the keeper too.

### Phase 2 milestones
1. `AgentVault` (ERC-4626 + roles + allowlists + caps + fees) in Foundry. 2. Fuzz/invariant suite.
3. `VaultKeeperBroker` (web3) wiring the agent as keeper. 4. Deposit/withdraw + vault FE. 5. Testnet
end-to-end. 6. **Audit.** 7. Mainnet w/ deposit cap → ramp.

---

## 5. Security checklist (must-pass before any real funds)

- [ ] **No path lets the keeper/agent move user funds to an external address** (trade-only; allowlisted router+tokens).
- [ ] Compromised keeper key → bounded damage only (slippage + size + cooldown caps); cannot drain.
- [ ] Users can always withdraw pro-rata, even with keeper paused (emergency exit).
- [ ] Reentrancy guards on deposit/withdraw/rebalance; CEI ordering.
- [ ] Slippage (`minReceived`) enforced on every swap; oracle/price sanity vs the swap.
- [ ] Owner = multisig; sensitive param changes behind a timelock; pausable.
- [ ] Deposit cap (start small) + per-tx caps; no unlimited approvals from the vault.
- [ ] High-water-mark perf-fee accounting (never charges principal, never double-charges).
- [ ] Foundry fuzz/invariant tests green; internal red-team; **professional audit** before mainnet.
- [ ] Treasury + keeper keys separate, hardware/multisig; key-rotation runbook.
- [ ] Phase 1: Permit2/exact approvals only; aggregator router is the (audited) approval target.

---

## 6. UX & growth

- **PnL showcase / leaderboard:** the agent's verifiable on-chain record (NAV curve, drawdown vs the
  30% line, trades, regime, the NL rationale per decision). Reuse the dashboard; make a public mode.
- **Trust signals:** "non-custodial," "trade-only keeper — we can't withdraw your funds," fee shown
  pre-trade, open-source contracts + audit report, live drawdown halt visible.
- **Onboarding:** connect → see the agent + its record → "rebalance with the agent" (P1) or "deposit"
  (P2) → clear fee/slippage disclosure → done. Mobile-friendly.
- **Honesty in copy (critical):** "disciplined, risk-controlled, transparent" — **never** "guaranteed
  returns / alpha." Past performance disclaimers. (See §9.)

---

## 7. Tech stack & repo layout (additive — doesn't disturb the contest agent)

```
contracts/                # NEW — Foundry: AgentVault.sol (ERC-4626), tests, deploy scripts (Phase 2)
src/ictbot/exec/vault_keeper.py   # NEW — web3 VaultKeeperBroker (Phase 2 keeper)
src/ictbot/data/aggregator.py     # NEW — 0x/1inch quote+fee adapter (Phase 1)
src/ictbot/api/                   # extend — public allocation feed + leaderboard endpoints
web/  (or a new web-app/)         # extend/new — swap widget, vault deposit/withdraw, PnL showcase
docs/monetization_plan.md         # this file
```
Reuse: the strategy/regime engine, NL rationale, dashboard, journaling, the hardened runtime.

---

## 8. Risks & honest caveats (don't skip)

- **Smart-contract risk (Phase 2):** a vault bug = users lose money. Non-negotiable: audit + caps +
  testnet-first + the trade-only keeper boundary. Phase 1 sidesteps this entirely (non-custodial).
- **Regulatory:** **pooling and managing other people's money for a fee can be a regulated activity**
  (collective investment / fund management) depending on jurisdiction. Phase 1 (non-custodial tooling
  + referral fee) is materially lower-weight than Phase 2 (custodial pooled vault). **Get legal review
  before Phase 2 mainnet** — this is a flag, not legal advice.
- **No-edge honesty:** we proved there's no reliable TA alpha on these majors. The product's pitch is
  **risk-controlled, transparent, disciplined management** — not alpha. Overpromising returns to users
  is both an ethics and a misrepresentation risk. Market the *process + transparency + drawdown
  control*, with clear past-performance disclaimers.
- **Key management:** treasury (fees) and keeper (trading) keys must be separate + hardware/multisig.
- **Aggregator dependency (Phase 1):** fee param + cap must be verified; have a fallback aggregator.

---

## 9. What we reuse vs build new

| Reuse (done) | Build new |
|---|---|
| Strategy/regime engine (target weights) | Aggregator adapter + builder-fee routing (P1) |
| NL rationale ("why it traded") | Swap widget / deposit-withdraw FE |
| Mission Control dashboard → public PnL showcase | `AgentVault` ERC-4626 + security suite (P2) |
| Hardened runtime (DD halt, idempotency, fast monitor) | `VaultKeeperBroker` web3 keeper (P2) |
| On-chain journal / verifiable record | Multisig treasury + key runbook |

---

## 10. Execution roadmap (once approved)

**Phase 1 (non-custodial, ship + demo):**
1. Public allocation-feed + leaderboard endpoints (reuse snapshot). 2. Aggregator adapter (verify fee
   param/cap on 0x **or** 1inch BSC). 3. Swap widget FE + wallet connect + fee/slippage disclosure.
4. PnL showcase page. 5. Testnet E2E + a guarded mainnet smoke (tiny). 6. (optional) session-key auto-follow.

**Phase 2 (managed vault, audit-gated):**
7. `AgentVault` (ERC-4626 + roles + allowlists + caps + hybrid fees) in Foundry. 8. Fuzz/invariant +
   red-team. 9. `VaultKeeperBroker` wiring the agent as keeper. 10. Vault FE (deposit/withdraw/PnL).
11. Testnet E2E. 12. **Professional audit.** 13. Mainnet w/ low deposit cap → ramp.

**Gate between phases:** Phase 2 mainnet does not proceed without (a) a clean audit and (b) legal review.

---

## 11. Open decisions for you (before we build Phase 1)

1. **Aggregator:** 0x vs 1inch on BSC (I'll verify each one's affiliate-fee cap + param and recommend).
2. **Builder fee bps:** 0.5% recommended — confirm (vs 0.3% / 1%).
3. **Auto-follow (session keys):** include the one-click auto-rebalance in Phase 1, or start with
   manual per-trade signing (simpler, even more obviously non-custodial)?
4. **Treasury wallet:** reuse the agent wallet `0xE8A3…6215`, or stand up a dedicated **multisig**
   treasury for fees (recommended)?
5. **Dapp surface:** extend the existing `web/` app, or a separate public "invest" app?

Answer these and I'll start Phase 1 with a robust, security-first, testnet-first implementation.
