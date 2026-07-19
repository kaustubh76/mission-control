# ICT v1 Strategy Specification

> Formal contract for `ictbot.strategy.ict_pro_max.ICTProMaxStrategy`.
> Reading this you can re-implement the strategy from scratch without
> reading the code.

## Inputs per evaluation

Four OHLCV DataFrames (columns `time, open, high, low, close, volume`):

| Frame    | Timeframe | Min bars | Purpose                       |
| -------- | --------- | -------- | ----------------------------- |
| `htf`    | 4h        | 50       | Higher-timeframe bias         |
| `bias`   | 15m       | 20       | Lower-timeframe bias (diag)   |
| `poi`    | 3m        | 20       | POI level + tap detection     |
| `entry`  | 1m        | 5        | MSS, FVG, delta, signal price |

Plus a `session` dict (Tokyo/London/NY status) and the pair name string.

## Output

A dict (or `Signal` view, ROADMAP §G1) carrying:

- Identity: `pair`, `error`.
- Prices: `price` (latest 1m close), `last_close`, `sl`, `tp`, `rr`.
- ICT stack values: `htf_bias`, `ltf_bias`, `ltf_poi`, `poi_tap`,
  `ltf_mss`, `fvg`, `micro_fvg`, `delta`, `relative_delta`,
  `delta_mode`, `atr_1m`.
- Signal: `entry` ∈ {BUY, SELL, NO ENTRY}, `confidence` ∈ {0,25,50,75,100}.
- Gates: `gate_blocked` (None or reason string), `regime` (HIGH_VOL etc.).
- `diagnostics`: per-direction blockers + closest direction.

## Pipeline

1. **Bias** (4h): `bias_engine` ∈ {sma, swing, slope}. Default `sma`.
   - `sma`: SMA20 > SMA50 → BULLISH else BEARISH.
   - `swing`: last two swing highs ASC + last two swing lows ASC → BULLISH;
     last two DESC of each → BEARISH; mixed → most recent swing direction.
   - `slope`: EWM-slope sign.
2. **POI** (3m): `poi_engine` ∈ {min_max, order_block}. Default `min_max`.
   - `min_max`: BULLISH → lowest low of last 20 bars; BEARISH → highest high.
   - `order_block`: last opposite-colour candle before the most recent
     same-direction swing pivot. With `mitigation_bars`, falls back to
     min_max if the OB has been tapped > N bars ago (E3).
3. **POI tap** (3m): price within `poi_tolerance` fraction of POI level.
   With `mitigation_bars` set on the legacy POI engine, tagged POIs
   retire after N bars.
4. **MSS** (1m): `mss_mode` ∈ {simple, swing}. Default `swing` (E2).
   - `simple`: BULLISH MSS when last bar's low > prev's high AND last
     close > prev high; mirror for BEARISH.
   - `swing`: real ICT — break of protected swing (high in a downtrend,
     low in an uptrend).
5. **Micro FVG** (1m): 3-bar imbalance. BULLISH FVG when `low[-1] >
   high[-3]`; BEARISH when `high[-1] < low[-3]`. With `mitigation_bars`
   set (E3), scan the last `mitigation_bars + 2` bars for the most
   recent unfilled FVG.
6. **Delta** (1m, last 1m window): `delta_mode` ∈ {sign, relative}.
   - `sign`: legacy. `delta > 0` for buy, `delta < 0` for sell.
   - `relative` (B3): `relative_delta = delta / median(|signed| over
     last 20 bars)`. Triggers when |relative| > `relative_delta_threshold`
     (default 0.5).
7. **ATR(14)** on 1m. Drives SL/TP distance when `sl_atr_mult` and
   `tp_atr_mult` are set; otherwise the `sl_frac`/`tp_frac` price
   fractions apply.

## Confidence

Four 25-point bits, max 100:
- POI tapped → +25
- "MSS" present in `ltf_mss` → +25
- FVG present OR `require_fvg=False` → +25
- Delta direction aligns with HTF bias → +25

Confidence is informational; entry/no-entry is binary on the boolean
stack above.

## Entry rules

`bullish_setup` (BUY before any fade flip):
- `gate_blocked is None`
- `htf_bias == BULLISH`
- `poi_tap == "POI TAPPED"`
- `ltf_mss == "BULLISH MSS"`
- FVG OK (either `require_fvg=False` or `micro_fvg == "BULLISH FVG"`)
- delta_buy (delta_mode-dependent, see step 6)

Mirror for `bearish_setup`. Only one direction can fire per bar.

### Fade flip

If `strategy_mode == "fade"`, the direction inverts and SL/TP mirror
around `price`:
- BUY → SELL with `sl' = price + (price - sl)`, `tp' = price - (tp - price)`
- SELL → BUY mirrored.

### Stop / TP

If `use_atr` (both `sl_atr_mult` and `tp_atr_mult` set, ATR > 0):
- BUY: `sl = price - sl_atr_mult * atr`, `tp = price + tp_atr_mult * atr`
- SELL: mirror.

Else fixed-fraction:
- BUY: `sl = price * (1 - sl_frac)`, `tp = price * (1 + tp_frac)`
- SELL: mirror.

All SL/TP prices then pass through `round_to_tick(price, tick_size)`
(E1 auto-discovered per pair via `BybitExchange.tick_size`).

## Gates

- `killzone_required`: when True, `gate_blocked = "outside killzone"`
  unless `session["killzone_active"]` is True. Killzone is "London or
  NY currently open" — bar-time aware in backtest (E5).
- `skip_in_low_vol`: when True, `gate_blocked = "regime is LOW_VOL"`
  if `atr_percentile_regime(entry_df) == "LOW_VOL"`.

A non-None `gate_blocked` short-circuits both setups to `NO ENTRY`.

## Friction model (engine-level, not strategy)

The backtest applies friction outside the strategy:
```
friction_R = 2 × (FEE_PER_SIDE + SLIPPAGE_PER_SIDE) / risk_distance_pct
```
where `risk_distance_pct = |price - sl| / price` is the ORIGINAL risk
distance (computed at entry; trailing SL doesn't change it).

Realised `net_R = gross_R - friction_R` per trade.

## RR floor

Per ADR 0005, live deployment is gated to RR ≥ 2:1 grids (`GRIDS["rr2plus"]`).
The strategy itself doesn't enforce this — the sweep/WFO grid choice
does. Configurations with `tp_frac / sl_frac < 2` exist in `GRIDS["default"]`
only for legacy comparison.
