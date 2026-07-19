# Free Data Sources (CoinMarketCap replacement)

Mission Control was built on CoinMarketCap. **CMC is no longer available**, so the agent now
sources every live read from **free, keyless public APIs** — no API key, no account. This is
controlled by a single flag, `FREE_DATA` (default **true**).

## The stack

| Need | Free source | Module / function | Endpoint |
|------|-------------|-------------------|----------|
| 4h close matrix (strategy) | **Binance klines** | `freefeeds.binance_klines(sym,"4h")` via `cmc.fetch_cmc_4h` fallback | `data-api.binance.vision/api/v3/klines?symbol=BNBUSDT&interval=4h` |
| 24-month daily seed | **Binance klines** | `cmc_intel.daily_ohlcv` → `freefeeds.binance_klines(sym,"1d")` | same, `interval=1d` |
| Fear & Greed (0–100) | **alternative.me** | `cmc.fear_greed` → `freefeeds.alt_me_fear_greed` | `api.alternative.me/fng/?limit=1` |
| Spot price (execution) | **Binance ticker** (→ DexScreener fallback) | `cmc.cmc_price` / `cmc.price` | `data-api.binance.vision/api/v3/ticker/price` |
| DEX signals: price, liquidity, 24h volume, change, txns | **DexScreener** | `dexscreener.dex_signals(symbols)` | `api.dexscreener.com/tokens/v1/bsc/{addrs}` |
| DEX search | **DexScreener** | `dexscreener.dex_search` (via `x402_cmc.dex_search`) | `api.dexscreener.com/latest/dex/search?q=` |

`data-api.binance.vision` is Binance's own public data host and, unlike `fapi.binance.com`, is **not
geo-blocked** from US/cloud IPs (Render), so it works everywhere the agent runs. All reads are
best-effort (never raise) and lightly TTL-cached, so the 4s dashboard poll never hammers a source.

## Design — swap behind stable seams

The migration keeps the existing (legacy `cmc_`-prefixed) function signatures, so every downstream
consumer — `align_close_matrix`, `strategy/adapters/momentum_cmc.py`, `agent/regime_report.py`,
`api/reads.py`, and the test suite — is unchanged. Only the *inside* of each seam swaps, gated
`settings.free_data and not settings.cmc_only`:

- `data/freefeeds.py` (new) — Binance klines/ticker + alternative.me F&G.
- `data/dexscreener.py` (new) — DEX signals, token price, search; `BSC_TOKEN_ADDRS` map for the 8
  contest tokens (mirrors the old `CMC_IDS` map).
- `data/cmc.py` — `fear_greed`, `cmc_price`, and `fetch_cmc_4h` (empty CMC stream → free Binance 4h).
- `data/cmc_intel.py` — `daily_ohlcv` → Binance daily.
- `data/x402_cmc.py` — `dex_search` → DexScreener (paid x402 leg dropped).
- `agent/regime_report.py` — `data_provenance` now names the free sources; the sold report carries a
  live `dex_signals` block.

## The sold report's provenance

Under FREE_DATA the ERC-8183 deliverable's provenance is honest about its free inputs:

```json
"data_provenance": "binance:4h-klines + alternative.me:fng + dexscreener:dex",
"cmc_sources": { "free_apis": ["binance:klines/4h+1d", "alternative.me:fng", "dexscreener:dex"], ... }
```

## vlayer Web Proof

The grant's vlayer Web Proof was retargeted from CMC to the **free alternative.me Fear & Greed**
endpoint — same clean integer, no API key. See `docs/vlayer/INTEGRATION_PLAN.md` and `vlayer/`.
`value` is a quoted string (`"25"`), so the prover reads it with `jsonGetString("data.0.value")` and
parses the digits with `_parseUint` (no float parsing).

## Verify

```bash
# live, no key:
curl "https://data-api.binance.vision/api/v3/klines?symbol=BNBUSDT&interval=4h&limit=3"
curl "https://api.alternative.me/fng/?limit=1"
curl "https://api.dexscreener.com/tokens/v1/bsc/0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

# offline unit tests:
pytest tests/test_free_data.py

# end-to-end (no CMC key): the close matrix + sold report on free data
CMC_API_KEY="" python -c "from ictbot.agent.regime_report import build_report; print(build_report()['data_provenance'])"
```
