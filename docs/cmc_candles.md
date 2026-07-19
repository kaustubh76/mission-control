# CMC-native candles & the zero-CEX data path (`CMC_ONLY`)

**Status: SIM-active, LIVE-ready (2026-06-15).** The contest agent decides and sizes **entirely on
CoinMarketCap data** — no centralized-exchange (CEX) candles, prices, or fallbacks anywhere on the running
path or in the validation we cite. This document is the architecture + the honest caveats.

> **Live-arm note (2026-06-20):** the live arm is now **mean-reversion** (`.env STRATEGY_NAME=mean_reversion`).
> The CMC-native data path + the `CMC_ONLY` firewall described below are **arm-agnostic** — they feed
> whichever arm is pinned. `momentum_cmc` (the CMC-native momentum variant) and `momentum_adaptive` remain
> registered arms; everything below still describes the data path verbatim.

> Why this exists: the contest is judged on a CMC-data agent. Earlier vintages ranked on Binance 4h candles
> (CMC has no historical intraday OHLCV on the Startup tier). This work makes the data genuinely CMC's own —
> selection on CMC's 4h candles, sizing on CMC Fear&Greed + CMC MCP technicals + the CMC Skills-Marketplace
> market-overview — with a hard firewall that makes any CEX reach **fail loud** instead of silently used.

---

## 1. The data problem and the unlock

CMC's Startup tier has **no historical intraday (4h) OHLCV** — verified across REST `ohlcv/historical`
(daily only), the 12 Agent-Hub MCP Data tools (TA, not candles), and the Skills Marketplace. The only
CMC-native, Startup-tier *price history* surfaces are:

- **CMC daily OHLCV** — `/v2/cryptocurrency/ohlcv/historical`, 24-month, geo-open. Real history, but daily.
- **CMC live price** — `quotes/latest` (REST) and the **CMC Pro WebSocket** (~15s ticks).

So we **accumulate 4h candles ourselves** from CMC's live feed, going forward, and seed the lookback warmup
from CMC daily. 100% CMC, no exchange.

## 2. The WebSocket streamer (`scripts/cmc_stream.py`)

- **Endpoint:** `wss://pro-stream.coinmarketcap.com/v1?CMC_PRO_API_KEY=…` (key from `.env`, never logged).
- **Channel / mode:** `market@crypto_latest_price`, `full` (Startup-tier). The **cracked subscribe frame**
  (the one-line puzzle that gated this — the crypto-ID key is `crypto_ids`):

  ```json
  {"method":"subscribe","channel":"market@crypto_latest_price","params":{"mode":"full","crypto_ids":[1839,1027,7186,1975,7083,5805,6636,74]}}
  ```
  A correct subscribe returns `ack: {"crypto_ids":[…]}` then streaming `type:"data"` frames
  `{"data":{"cid":<CMC_ID>,"p":<price>,"vu":<vol24h>},…}`.

- **Bar building (`BarBuilder`):** each tick folds into the in-progress 4h OHLC bar for its symbol on the UTC
  4h grid (00/04/08/12/16/20). When the next bucket's first tick arrives, the completed bar is finalized to
  the shared cache: `cache.write("cmc", f"{sym}/USDT", "4h", df)` → `data/cache/cmc/<slug>/4h.parquet`
  (columns `[time,open,high,low,close,volume]`, merge+dedup keep-latest, so re-finalizing is idempotent).
- **Robustness:** auto-reconnect with backoff, WS keepalive ping, a heartbeat file
  (`data/logs/cmc_stream_heartbeat.ts`), and a partial-bar checkpoint (`data/cache/cmc_4h_partial.json`) so a
  restart resumes the same bar instead of dropping it.

## 2b. The full CMC-WS channel harvest (beyond price)

The streamer originally read only `p`/`vu` off the `crypto_latest_price` frame. CMC's WebSocket
carries much more, and the team probed it empirically (`scripts/cmc_stream.py --probe/--discover`
→ `data/journal/cmc_ws_capability.json`, the capability map that gates every subscribe, mirroring
`cmc_intel`'s endpoint map). Ground truth, not reverse-engineered guesses:

**(a) CEX channel — same `crypto_latest_price`, full payload.** `full` mode streams 14 fields, not
2. Beyond price/volume it carries **market cap (`mc`), circulating supply (`cs`)**, and the whole
**percent-change window family** `p24h/p7d/p30d/p60d/p3m/p1y/pytd/pall` (verified `mc = p × cs`).
`QuoteSnapshotWriter` harvests these — additively, wrapped best-effort so it can never regress the
bar path — into `data/cache/cmc_ws/quotes.json`. The read layer is `data.cmc_stream_store`
(zero-network, staleness-gated, never-raise). First payoff: **`cmc_intel.token_changes` now serves
the universe tilt's 7-day strength from this local snapshot (0 credits) instead of a REST
`quotes/latest` call**, falling back to REST only when the stream is stale/thin. The tick journal
records which served it (`quote_source: cmc_ws | rest`).

**(b) On-chain (DEX) channels — the `onchain@*` family (BNB-chain, all 8 universe tokens).** Keyed
by each token's **BNB-chain BEP-20 contract** (`platform_id=14`, CMC-derived via
`/v2/cryptocurrency/info`, in `cmc_onchain.py::ONCHAIN_TOKENS`) — the contest universe is BEP-20, so
this reflects real BNB-chain activity. Streaming with just the token address: `onchain@token_metric`
(buy/sell vol, **unique traders**, txns, h/l per 1m/5m/1h/4h/24h), `onchain@holders_metrics`
(top-10/50/100 **concentration**), `onchain@liquidity_event` (add/remove/migrate USD),
`onchain@token_agg_event` (aggregated price + **`lu` = total token liquidity**, the broad
liquidity-depth signal), `onchain@transaction` (per-swap firehose → aggregated into **whale-flow**:
net USD of swaps ≥ `ONCHAIN_WHALE_USD` over 1h). (`onchain@pool_metric` — per-pool unique traders,
keyed by **pool** address — was **dropped**: it streamed fine but no strategy/overlay consumed it,
so subscribing/parsing it was pure dead weight.)

**Correction (supersedes an earlier note):** the CMC **DEX REST IS accessible on the Startup key** —
the earlier "unprovisioned" read was a param mistake. `/v4/dex/spot-pairs/latest` (pool discovery:
numeric `network_id=14` + `dex_slug=pancakeswap-v2` + `base_asset_symbol` + `sort=liquidity`) and
`/v4/dex/pairs/quotes/latest` (per-pool **liquidity depth** + 24h volume) both return 200 —
`cmc_onchain.derive_pools()`/`dex_quotes()`. Discovery is **V2-only** (the V3 slug doesn't resolve),
so it mainly yields CAKE; `token_agg_event.lu` covers liquidity for the rest. **`onchain@kline` and
the `/v4/dex/pairs/ohlcv|trade` REST endpoints are genuinely undeliverable** (kline streams no data
even on a 469M-volume pool; the OHLCV/trade/networks/listings endpoints 500) — there is **no
per-pool DEX-candle route** on this tier.

The on-chain feed runs on a **separate WS connection** (`_stream_onchain`, supervised independently)
gated by **`CMC_ONCHAIN_ENABLED`** — the 4h bar feed is byte-identical regardless. Frames carry no
channel tag → classified by shape (`cmc_onchain.classify_frame`) and routed to a token by contract
address.

**Channel surface is complete (verified by probe).** Every useful CMC WS channel is wired. The
remaining ones add nothing for strategy data: `onchain@kline` is undeliverable (above);
`onchain@unique_trader` is redundant (`token_metric.ut`); and the speculative `market@kline /
quote / global_metrics / fear_and_greed / trending` channels are all **rejected by the server**
(they don't exist — CMC's WS has only the one CEX channel + the `onchain@*` family). One future
option: `onchain@holder_wallet_update` acks but needs *specific* whale wallet addresses to track —
useful only with a top-holder-address discovery step (e.g. seeded from `holders_metrics`), as an
early whale-exit signal. Not wired today.

## 2c. Channel ↔ strategy wiring (the signal buffet)

`strategy.market_signals.token_signals()` merges every per-token signal (CEX multi-window
`pct_24h/7d/30d` + `volume_24h` + `market_cap`; on-chain `flow_ratio`, `unique_traders`,
`liquidity_usd` (`lu`), `top10_pct`, `net_liquidity_usd`, `whale_net_usd`) into one zero-network,
staleness-gated buffet. Each strategy archetype pulls what it needs, via **live overlays**
(`strategy.universe_overlay`, the same place as `momentum_tilt`) — never the array-based backtest core:

| Lever (flag) | What it does | Strategies |
|---|---|---|
| `ALLOC_UNIVERSE_TILT` | 7d relative-strength tilt (0-credit WS snapshot) | momentum family |
| `ALLOC_FLOW_W` | on-chain buy/sell `flow_ratio` tilt, clamped `[0.85,1.15]` | momentum family |
| `ALLOC_MIN_VOL_USD` | drop rank candidates below a 24h-volume floor | all |
| `ALLOC_MAX_TOP10_PCT` | halve weight of over-concentrated (whale-risk) tokens | all, esp. mean-reversion |
| `ALLOC_LIQ_BRAKE` | deploy-cap haircut on net DEX-liquidity/whale **outflow** (risk-reducing) | all |

These are **live-only** (no on-chain history to A/B) and **journaled each tick** (`onchain_signals`)
for forward-validation. Bounds keep them safe: tilts re-weight WITHIN the held set (same deployment),
the cap brake only LOWERS it. The contest `.env` enables the high-value ones (aggressive posture);
each is independently reversible, and a stale/absent feed degrades every overlay to a no-op.

Probe/operate:

```bash
PYTHONPATH=src python scripts/cmc_stream.py --probe --probe-id 1839   # one CEX frame, all fields
PYTHONPATH=src python scripts/cmc_stream.py --discover                # sweep → capability map
PYTHONPATH=src python scripts/cmc_stream.py --onchain-once 30         # on-chain smoke (no bars)
CMC_ONCHAIN_ENABLED=true …                                            # enable the on-chain feed
```

## 3. Durable ops (`scripts/cmc_stream.sh` + watchdog cron)

The streamer is long-running (unlike the cron-tick services). `scripts/cmc_stream.sh` is an **idempotent
launcher + watchdog**: if the process is alive AND the heartbeat is fresh (< 180 s) it does nothing; if the
process died or hung, it kills any stale instance and restarts via `nohup`. Cron it every 5 minutes so
candles accrue continuously to the contest:

```cron
*/5 * * * * "/Users/apple/Desktop/BNB-Hack-CMC/scripts/cmc_stream.sh" >> /Users/apple/Desktop/BNB-Hack-CMC/data/logs/cmc_stream_watchdog.log 2>&1 # bnb-cmc-stream
```

Status: `pgrep -fl cmc_stream.py` · `tail -f data/logs/cmc_stream.log`. If the watchdog ever lapses, the
cold-start seed (below) still covers the momentum lookback; only intrabar high/low resolution is lost.

## 4. Cold-start seed + the candle sources (`src/ictbot/data/cmc.py`)

- **`seed_cmc_4h_from_daily(tokens)`** — backfills the 4h cache from CMC **daily** closes forward-filled onto
  the past 4h grid (six identical slots per day). 100% CMC; momentum (close-to-close) stays exact. Only
  completed past bars are seeded; real streamed bars overwrite the seed (cache keep-latest).
- **`fetch_cmc_4h(symbol)`** — reads the streamed 4h cache + appends the in-progress partial bar so a live
  tick sees the freshest CMC price.
- **`cmc_4h_close_matrix(tokens)`** — aligned 4h close matrix for the live arm; auto-seeds if < 250 bars.
- **`daily_close_matrix(tokens)`** — aligned real CMC **daily** matrix; the **CEX-free DQ-safety backtest**.

### The flat-daily-seed inverse-vol bug (and the fix)
The seed writes six identical 4h closes per day → a token whose recent window is (near-)constant collapses its
30-bar return-std → `1/vol` explodes → it captures ~100% of the deployment on a seed artifact. Fix: a per-tick
**`vol_floor`** on `AllocatorParams` (default `0.0` — a strict no-op, so the locked `momentum_adaptive` arm is
byte-identical). `CMCMomentumStrategy` derives the floor from CMC's own **daily** vol: cross-token median of
the last ~30 daily-return stds, rescaled to the 4h bar by `1/√6` (a daily return ≈ Σ of six 4h returns →
std scales with √6). During the seed window all tokens clamp to the floor → equal-weight (the honest "no real
intrabar vol yet" stance); once real 4h bars dominate the window (well before the contest — `vol_lookback`=30
bars = 5 days) the true inverse-vol tilt returns. Pinned by `tests/test_adapter_momentum_cmc.py`.

## 5. The zero-CEX firewall (`CMC_ONLY`)

Every CEX path in the codebase funnels through one chokepoint, `cmc.fetch_4h` (the allocator's `binance_4h`
branch, `price()`'s last-close fallback, the validation scripts). Setting **`CMC_ONLY=true`** makes
`fetch_4h` **raise** rather than silently serve exchange data, and routes `price()`'s execution-sizing
fallback to the CMC stream (`cmc_price` → `fetch_cmc_4h`). It is **boot-guarded** to require
`CMC_INTEL_ENABLED=true` (the seed needs CMC daily OHLCV) — otherwise the arm would silently brick. The
firewall is set at the **momentum_cmc entry points** (`scripts/forward_tick.sh`, `scripts/live_tick.sh`,
`make forward_track_cmc`), NOT globally in `.env`, so the separate `forward_tracks.sh` research arms (legacy
CEX comparison) are unaffected. Pinned by `tests/test_cmc_only_firewall.py`.

## 6. The full CMC decision stack

`momentum_cmc` (`src/ictbot/strategy/adapters/momentum_cmc.py`) subclasses the locked `AdaptiveMomentumStrategy`
— identical ranking/cap machinery, fed CMC data via `candle_source="cmc_4h"`. The live tick
(`scripts/run_allocator.py`) threads the whole CMC stack into the decision:

| CMC surface | Flag | Role |
|---|---|---|
| CMC 4h candles (stream + seed) | `candle_source=cmc_4h` | token **selection** (momentum ranking) |
| CMC Fear&Greed (`/v3/fear-and-greed`) | always | deploy-cap regime term |
| CMC MCP technicals (RSI/MACD/EMA, basket health) | `ALLOC_TA_ENABLED` | rank tilt (`ta_rank`) + cap term |
| CMC Skills market-overview (composed) | `CMC_SKILL_REGIME` | risk-budget → cap |
| CMC derivatives stress | `CMC_DERIV_BRAKE` | multiplicative cap brake (live-only) |
| CMC macro events | `CMC_MACRO_GUARD` | cap haircut into high-impact events (live-only) |
| CMC global metrics (dominance/mktcap) | `CMC_REGIME_ENHANCED` | macro-regime cap term |

Provenance is journaled per tick: `strategy`, `candle_source`, `ta_source`, `cmc_skill` (with the
`tools_used` MCP list), and surfaced through the API (`reads.rebalances_card` → `schemas.RebalanceItem`;
`candle_source` is a **declared** schema field — `response_model=SnapshotOut` silently strips undeclared keys).

## 7. Validation — CEX-free, honest

- **DQ-safety gate (cited):** `make validate_allocator` now defaults to `--candle-source cmc_daily` — the
  rolling-7-day distribution + Gate-A verdict on **real CMC daily history**. At the live `top_k=5`,
  diversification keeps every cap band DQ-safe; the running band **[0.35, 0.80] → 23.5% worst-week DD**,
  `--cap-sweep` reports the full table. The live `cmc_4h` feed is *less* volatile than daily, so this is a
  conservative bound.
- **Per-lever A/B (CEX-free):** `make ab_regime` (extended with `--candle-source cmc_daily`) grades each
  enrichment on CMC daily. Result: **`ta_rank` PASSES** (+5.3 pts risk-penalized return, −0.2 pts DD,
  DQ-safe → ON); the derivatives/macro/skill brakes are **risk-reducing by construction** (they only lower
  the cap) and live-only (forward-validated); the multi-timeframe blend `ranking` is NOT enabled (it was the
  one non-DQ-safe lever). All enabled levers are DQ-safe.
- **Forward:** `make forward_track_cmc` runs the exact contest config (firewall + full stack) into an
  isolated tree; `make forward_track_cmc_report` matures the verdict over the contest week as real 4h bars
  accrue.

## 8. Honest caveats (surfaced, not buried)

- **Forward-only 4h data.** The stream builds candles going forward; deep history is the CMC-daily seed. The
  contest week's decisions run on real CMC 4h bars; the seed only covers the lookback warmup.
- **The backtest is on CMC *daily*** (a conservative proxy for the 4h forward), because CMC has no 4h history.
  DQ-safety is proven there + confirmed forward — never on an exchange feed.
- **No edge claim.** Long-only spot has no fixed edge; the stack's value is DQ-safety (risk-reducing brakes) +
  better selection (`ta_rank`) + full CMC-native provenance. The agent manages *exposure*, validated forward.
- **Two flags required.** The contest config is `CMC_ONLY=true` **and** `CMC_INTEL_ENABLED=true` (boot-guarded).
- **Ops dependency.** The watchdog must run; if it lapses, the seed still covers the lookback (intrabar
  resolution degrades, momentum does not).
- **Locked arm untouched.** `momentum_adaptive` (Binance-4h) stays registered + byte-for-byte as the dormant
  fallback; `CMC_ONLY` makes it unreachable on the contest config without deleting any code.
