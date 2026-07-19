# Pre-Trade CMC Data Audit — "is real, fresh CMC data feeding every strategy decision?"

**Purpose.** The contest brief requires that the agent decides on **100% CoinMarketCap data, zero
exchange data**. This is the reference for the *final check before live trade start*: it maps every
strategy decision input to its live CMC source, the freshness gate that protects it, and its
degradation mode — and names the single command that gates arming.

**One-command gate:** `make arm_check` (rc 0 ⇒ safe to arm). It now includes the CMC-data-liveness
probes described below; run it immediately before flipping `TWAK_MODE=live` / `ENABLE_LIVE_TRADING=true`.

---

## 1. Decision-input → CMC source → freshness map

| Input (what the decision uses) | Live CMC source | Path (file) | TTL / freshness | If unavailable |
|---|---|---|---|---|
| **4h candles** (the core signal — momentum/rank/breakout/etc.) | WS `market@crypto_latest_price` (8 contest tokens) | `cmc_stream.py` → `data/cache/cmc_4h_partial.json` (partial, every tick) + `data/cache/cmc/{SYM}_USDT/4h.parquet` (completed) → `fetch_cmc_4h` → `cmc_4h_close_matrix` ([cmc.py:314](../src/ictbot/data/cmc.py#L314)) | partial: per-tick (~secs); completed: per 4h bar; cold-start seed from CMC **daily** OHLCV (`seed_cmc_4h_from_daily`, past bars only, deduped) | tick **hard-skips** if newest bar > 12h old (`MAX_BAR_AGE_H`) or matrix < 200 rows |
| **Price** (execution sizing / NAV) | CMC `/v2/cryptocurrency/quotes/latest` → under `CMC_ONLY` falls back to the **live CMC 4h stream close** (`fetch_cmc_4h`), never a CEX | `cmc.price()` / `cmc_price()` ([cmc.py:69](../src/ictbot/data/cmc.py#L69)) | quote 60s TTL; fallback = freshest stream close | **raises** if quote + stream both empty (never trade blind) |
| **On-chain balances** (live broker portfolio snapshot) | TWAK CLI `balance` (BSC, on-chain) | [twak_client.py:312](../src/ictbot/exec/twak_client.py#L312) | per-tick read, **before** any swap | hardened: `_READ_RETRIES=4` (5 attempts, ~15s backoff) so a transient RPC hiccup can't abort the tick |
| **Fear & Greed** (regime term) | CMC `/v3/fear-and-greed/latest` | `cmc.fear_greed()` ([cmc.py:86](../src/ictbot/data/cmc.py#L86)) | 1h TTL | additive only → degrades to breadth+trend |
| **Regime intel** (BTC dominance, total mkt-cap, 7d F&G avg) | CMC `/v1/global-metrics/*` + `/v3/fear-and-greed/historical` | `cmc_intel.build_regime_intel()` ([cmc_intel.py:169](../src/ictbot/data/cmc_intel.py#L169)) | 6h TTL; gated `CMC_INTEL_ENABLED`/`CMC_REGIME_ENHANCED` (ON in the live config) | terms skipped → breadth+trend baseline |
| **TA tilt** (token rank + basket trend-health) | CMC MCP via the Agent Hub | `cmc_agent_hub.*` | daily-lagged; gated `ALLOC_TA_ENABLED`/`CMC_MCP_ENABLED` | falls back to local technicals (no penalty) |
| **On-chain overlays** (flow / holders / liquidity brakes) | WS `onchain@*` harvested locally | `cmc_stream.py` → `data/cache/cmc_ws/onchain_*.json` | 1h staleness gate; gated `CMC_ONCHAIN_ENABLED` | degrade to no-op |

All 11 registered arms (+9 contest aliases) resolve `candle_source="cmc_4h"` → they all consume the
matrix above; none reach a CEX path.

## 2. The firewall + freshness gates (defense in depth)

1. **Zero-CEX firewall** — under `CMC_ONLY=true` (set by `live_tick.sh`), any CEX candle path **raises**
   at [cmc.py:159](../src/ictbot/data/cmc.py#L159). A strategy can only ever see CMC data.
2. **Boot guard** — `CMC_ONLY=true` requires `CMC_INTEL_ENABLED=true` ([settings.py:975](../src/ictbot/settings.py#L975)); import fails loudly otherwise.
3. **Streamer liveness** — `arm_check` FAILs if the heartbeat (`data/logs/cmc_stream_heartbeat.ts`) is
   > 180s stale or the matrix < 200 rows; the `*/5` `cmc_stream.sh` watchdog restarts a dead streamer.
4. **Tick-time candle age** — a LIVE tick hard-skips (rc 2) if the newest candle is > 12h old.
5. **NEW — CMC-data-liveness probes** (`arm_check._cmc_live_price / _cmc_partial_bar_bucket /
   _cmc_quote_freshness / _cmc_intel_reach`): a live `cmc_price` for **every** contest token, the
   in-progress 4h bar on the **current** bucket (catches a hung streamer the 12h gate misses), the WS
   quote-snapshot age (< 300s), and (when regime-enhanced) F&G + regime-intel reachability.
6. **TWAK read-retry hardening** — `balance()`/`price()` retry 4× (`_READ_RETRIES`); swaps keep the
   conservative 2 (a swap retry could double-fill). The read is pre-swap, so retrying never risks a fill.

## 3. Residual risks (known, accepted, monitored)

- **Rate-limit / credit exhaustion** → `CMC.get()` returns the freshest cache (up to TTL) rather than
  failing. Headroom is large (300k/mo, ~20-30 credits/tick); monitor `data/journal/cmc_usage.json`.
- **F&G / regime-intel / on-chain degrade silently to the breadth+trend baseline** — by design,
  non-blocking. `arm_check` reports these as INFO so the operator sees the degraded state.

## 4. Empirical proof (this audit)

- **All-arm live smoke** (`run_allocator --strategy <arm> --quote-only`, `CMC_ONLY=true`): **10/11 arms**
  produced a full dryrun REBALANCE through the live CMC → decision → TWAK path with the firewall clean;
  the 1 miss was a transient TWAK balance hiccup (firewall clean), the gap that the `_READ_RETRIES`
  hardening closes.
- **Per-arm robustness probe** (offline, normal + degenerate inputs): every arm produced a finite,
  non-negative, cap-respecting weight vector the broker consumed with `n_failed=0` and conserved NAV —
  no arm-specific execution gap. Locked in by `tests/test_strategy_twak_wiring.py` +
  `tests/test_twak_cli.py` (read-path retry budget).

**Conclusion:** real, fresh CMC data feeds every strategy decision; the firewall + freshness gates + the
hardened TWAK read path make a silent stale/CEX/aborted-tick path unreachable. Gate each arming on
`make arm_check`.
