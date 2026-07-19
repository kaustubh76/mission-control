# CMC enablement — measure-first runbook

> **What CMC data/skills the agent uses, the measured evidence for each, and the safe path to enable
> more.** Companion to `make cmc_check` (live status), `make ab_regime` ([cmc_pnl_ab.md](cmc_pnl_ab.md),
> the PnL A/B), and `make mcp_check` ([mcp_wiring.md](mcp_wiring.md), the live MCP + skill-pairing proof).
> Generated 2026-06-14. **Measure-first: no flags were flipped to write this.**

## TL;DR — CMC is already lit, and the A/B says it's the *right* levers

Two surprises the measurement surfaced:

1. **`make cmc_check` shows CMC is fully flowing in the running config** — `CMC_API_KEY` set (9/9
   endpoints in-tier), Fear&Greed LIVE, regime intel LIVE (`cmc_intel_used=True`), TA cap+rank LIVE
   (`ta_health` from `cmc+skill`, `ta_rank_used=True`), market skill LIVE, MCP on. The *code defaults*
   are OFF (so the validated baseline stays bit-for-bit), but the operator's `.env` already enables the
   levers. "CMC not used up to the mark" is **false for the live config** — it *is* used.
2. **`make ab_regime` confirms the enabled levers are the right ones** — at the binding 0.70% friction,
   risk-penalized return (`totalRet − worstWeekDD`), DQ-safe:

   | Lever | Δscore vs baseline | Verdict |
   |---|--:|:--|
   | `enhanced` (regime intel: dominance/mktcap/F&G-momentum) | **+3.3** | ✅ TURN ON |
   | `ta_cap` (CMC TA → deploy cap) | **+2.9** | ✅ TURN ON |
   | `ta_rank` (CMC TA → ranking tilt) | **+3.2** | ✅ TURN ON |
   | `enhanced+ta` (the combination) | **+4.2** (best) | ✅ TURN ON |
   | `tilt` (universe relative-strength tilt) alone | −1.2 | ⚪ keep off |
   | `ranking` (multi-TF blend) alone | −4.6 | ⚪ keep off |
   | `full_cmc` (everything stacked) | −3.7 | ⚪ keep off |

   **Lesson: enable the *measured-good* subset (`enhanced` + TA), NOT everything.** Over-stacking
   (`full_cmc`, `tilt`/`ranking` alone) *hurts* risk-penalized return — more signal ≠ better.

## Per-lever reference

| Source / skill | Flag(s) | What it does | Backtestable? | Evidence |
|---|---|---|:--:|---|
| 4h candles | — | momentum ranking input | n/a | **Binance** (CMC intraday tier-gated, by design) |
| Fear & Greed | `CMC_API_KEY` | sentiment term in the regime cap | live-only | LIVE iff key set; else degrades to breadth+trend |
| Regime intel | `CMC_INTEL_ENABLED`+`CMC_REGIME_ENHANCED` | dominance/mktcap/F&G-momentum terms | ✅ (macro history) | **A/B +3.3 → ON** |
| TA → cap | `ALLOC_TA_ENABLED`+`ALLOC_TA_W_CAP>0` | CMC MACD/RSI/EMA breadth → deploy cap | ✅ (local fallback) | **A/B +2.9 → ON** |
| TA → rank | `ALLOC_TA_ENABLED`+`ALLOC_TA_W_RANK>0` | CMC TA tilt on the token ranking | ✅ | **A/B +3.2 → ON** |
| Market skill | `CMC_SKILL_REGIME` | composed risk-budget blended into the cap | live-only | not backtestable → forward-validate |
| MCP agent-hub | `CMC_MCP_ENABLED` | live source for TA/skill reads (else local) | n/a | plumbing for the above |
| x402 DEX | `X402_ENABLED` | paid on-chain DEX data | n/a | **ENRICH-ONLY** — journaled, never trades |

## The honest framing — GATE vs measured ON

- The **validated backtest baseline** (`make validate_allocator`) is the levers-**OFF** reference (the
  bit-for-bit contest number). Enabling levers **deviates** from that baseline.
- The deviation is now **measured**: `enhanced`+TA improve risk-penalized return *and stay DQ-safe*, so
  turning them on is evidence-backed, not a guess. The **live-only** levers (Fear&Greed, market skill,
  x402) can't be backtested — they're validated **forward in SIM** + operator judgment.
- So the enable path stays: **enable in `.env` → SIM-forward-validate (`make sim_test_all` / forward
  track) → operator sign-off.** Promotion to the contest is never automatic.

## How to enable / verify / roll back

```bash
make cmc_check                 # current live status (what's flowing) — changes nothing
make ab_regime                 # re-measure the backtestable levers' PnL effect (cmc_pnl_ab.md)
# enable a lever (operator, .env):  CMC_REGIME_ENHANCED=1  ALLOC_TA_ENABLED=1   (the measured-good subset)
make forward_track_all && make sim_test_all   # SIM-validate the enabled config end-to-end
# roll back: unset the flag in .env → the validated levers-OFF baseline returns bit-for-bit
```

**Do NOT** blanket-enable everything (`full_cmc`) — the A/B shows it *underperforms* the measured-good
subset. Keep `x402` as enrichment-only (it never drives a trade). Re-run `make ab_regime` after any
universe/data change — the verdict is data-window-sensitive, like every backtest here.
