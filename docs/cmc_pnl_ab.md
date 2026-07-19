# CMC PnL A/B — does the enhanced regime / tilt / ranking improve simulated PnL?

_Generated 2026-06-14T05:31:52.396665+00:00 · 2500 4h bars × 8 tokens (2025-04-23 → 2026-06-14) · 2298 rolling-7d windows · macro=yes._

## How to read this

The momentum engine + candles are held **constant**; only the deploy-cap source (baseline vs CMC macro), the within-set tilt, and the ranking change — so any difference IS that lever's PnL contribution. The strategy's edge is **exposure management**, not alpha (no fixed edge; entry-regime can't predict the next week), so it is judged on the **risk-penalized return** `score = total_return − worst_week_dd` and **DQ-safety** (`pct_dd_over_30 == 0`), at the contest-realistic 0.70%RT friction. Returns are cumulative over a down-leaning ~14-month window, so they are negative across the board — the question is which lever **loses less / draws down less**.

## Results (0.70%RT)

| arm | total return | worst-week DD | max DD | %weeks up | trades/wk | score |
|---|---:|---:|---:|---:|---:|---:|
| baseline | -45.56% | 15.6% | 60.3% | 32% | 15.4 | -0.612 |
| tilt | -46.55% | 15.8% | 60.9% | 32% | 15.4 | -0.624 |
| ranking | -50.36% | 15.4% | 62.2% | 31% | 16.4 | -0.657 |
| **enhanced** | -41.24% | 16.6% | 57.8% | 37% | 15.4 | -0.578 |
| **enhanced+tilt** | -42.20% | 16.6% | 58.1% | 37% | 15.4 | -0.588 |
| full_cmc | -48.27% | 16.6% | 61.0% | 35% | 16.4 | -0.648 |
| **ta_cap** | -43.08% | 15.2% | 58.4% | 34% | 15.4 | -0.582 |
| **ta_rank** | -42.33% | 15.6% | 57.7% | 33% | 15.5 | -0.579 |
| **enhanced+ta** | -40.80% | 16.2% | 57.5% | 37% | 15.4 | -0.570 |
| full_cmc+ta | -46.94% | 16.2% | 58.3% | 34% | 16.3 | -0.631 |

## Verdict

- **tilt** — WORSE: Δscore -1.2pts, Δworst-week-DD +0.2pts, DQ-safe yes → keep off
- **ranking** — WORSE: Δscore -4.6pts, Δworst-week-DD -0.2pts, DQ-safe yes → keep off
- **enhanced** — PASS: Δscore +3.3pts, Δworst-week-DD +1.0pts, DQ-safe yes → **TURN ON**
- **enhanced+tilt** — PASS: Δscore +2.4pts, Δworst-week-DD +1.0pts, DQ-safe yes → **TURN ON**
- **full_cmc** — WORSE: Δscore -3.7pts, Δworst-week-DD +1.0pts, DQ-safe yes → keep off
- **ta_cap** — PASS: Δscore +2.9pts, Δworst-week-DD -0.4pts, DQ-safe yes → **TURN ON**
- **ta_rank** — PASS: Δscore +3.2pts, Δworst-week-DD +0.0pts, DQ-safe yes → **TURN ON**
- **enhanced+ta** — PASS: Δscore +4.2pts, Δworst-week-DD +0.6pts, DQ-safe yes → **TURN ON**
- **full_cmc+ta** — WORSE: Δscore -2.0pts, Δworst-week-DD +0.6pts, DQ-safe yes → keep off

## Recommendation

**Turn ON: enhanced, enhanced+tilt, ta_cap, ta_rank, enhanced+ta** (best single arm: **enhanced+ta**) — with the principled default term weights. Two CMC levers clear the bar on this window:
- **Enhanced regime** — folds the CMC **macro** (BTC-dominance / total-mktcap / F&G-momentum) into the deploy cap; improves the risk-penalized return.
- **Technical analysis** — folds CMC's pre-computed **RSI / MACD / EMA** (daily) into the deploy cap (`ta_cap`) and the token ranking (`ta_rank`); `ta_cap` uniquely **cuts worst-week drawdown**, and **`enhanced+ta`** (macro + TA in the cap) is the strongest config. Backtested locally on the candle history; LIVE reads CMC's authoritative pre-computed TA via the Agent Hub MCP — same signal, compute offloaded to CMC.
**Per SIM-first: enable on the SIM track and forward-validate before promoting to LIVE**; the contest entry stays on the validated baseline until then. Over-stacking every lever (`full_cmc`, `full_cmc+ta`) and the bare tilt/multi-TF ranking are **neutral/negative** here — keep them OFF.

---

_Honest caveats: a single ~14-month window (warmup eats ~27 days); F&G history ~500 days covers it; a down-leaning sample so all returns are negative; the forward SIM A/B is the real arbiter. Data provided by CoinMarketCap._
