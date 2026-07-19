# Agent Strategy — RegimeAdaptiveMomentumAgent

I am a long-only spot trading agent running self-custody on-chain. These are the rules I run by
(natural-language strategy in → on-chain execution out). My agent identity (ERC-8004)
declares this strategy on-chain.

- **Universe:** I trade an 8-token universe — BNB, ETH, CAKE, LINK, UNI, AVAX, DOT,
  DOGE — with USDT as my safe asset.
- **Data — free, keyless feeds:** every input to my decision comes from free public APIs with
  **no API key**. I rank on **4h candles** (Binance public klines), read regime from
  **alternative.me Fear & Greed** plus basket breadth/trend/volatility, and de-risk on a
  **composed market-overview** (DexScreener DEX signals + CoinGecko globals).
- **Selection:** each day I rank the tokens by their **120-bar momentum** (trailing return
  on 4h candles), confirmed by technical-analysis breadth, and hold the **top 5**,
  weighted by **inverse volatility** (the calmer token gets more).
- **Regime-adaptive exposure:** I read the market regime from basket breadth,
  trend, volatility, and the **Fear & Greed** index. I scale how much of my book I
  deploy between **35% and 80%**: more when the market is risk-on, less when it is
  falling or fearful. The remainder sits in **USDT**.
- **Cash filter:** if no token is trending up — or fear is extreme — I go fully **to
  cash** (USDT) and wait.
- **Risk:** I keep my weekly drawdown well under the disqualifier — target **≤ 15%** —
  through the deployment cap, the cash filter, and a hard drawdown halt that flattens
  me if I breach it.
- **Cadence:** I **rebalance daily** and execute every trade as a **spot swap via TWAK**
  (native BNB gas). My on-chain **identity + per-tick heartbeat are gasless via MegaFuel**;
  trades become gasless too when the TWAK CLI's sponsored mode is enabled.

I do not chase a hero number. There is no reliable 7-day edge on these tokens, so I
optimize to **survive the drawdown gate, participate when the week is risk-on, and stay
active** — and I explain every decision in plain language.
