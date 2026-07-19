# Live-arming runbook — the single operator checklist

> **Status: NOT armed (SIM is the default).** This is the one place that consolidates every step to take
> the bot from the SIM/forward-paper track to **real BSC swaps via TWAK** for the contest week, and back.
> The detailed *why* lives in [bnb_strategy_decision.md §8](bnb_strategy_decision.md) (arming rationale)
> and [strategy_validation_runbook.md](strategy_validation_runbook.md) (the validation gate that precedes
> this); the *how* is here. Every command/flag below is the real one — cross-checked against
> `scripts/run_allocator.py`, `scripts/{live_tick,dd_watch,daily_floor}.sh`, and
> `src/ictbot/runtime/kill_switch.py`.

**Contest week:** 2026-06-22 → 06-28 (the held-out window). **Arm:** Jun 20–21 (operator) — arming earlier
just churns the small live balance. **Default arm:** the locked `momentum_adaptive` (`top_k=2`); LIVE is
strategy-agnostic via `STRATEGY_NAME` and never reads the SIM dashboard selector (contest-safety).

Two `.env` flags are **coupled** and both must agree, or the bot refuses to boot:
`ENABLE_LIVE_TRADING=true` **requires** `TWAK_MODE=live` (`settings.py` boot-guard), and `TWAK_MODE=live`
**requires** TWAK creds present. So arming is a single coherent flip, not a half-state.

---

## Step 1 — Pre-arm gate (must pass before touching `.env`)

```bash
# 1a. Locked default still bit-for-bit + DQ-safe + ACTIVE (>=7 t/wk, DD < 25% rail, < 30% DQ line):
make validate_allocator

# 1b. Full validation rollup (gate -> scoreboard -> stability -> readiness):
make validate_all                       # or the individual steps in strategy_validation_runbook.md

# 1c. Dry-run the LIVE setup — checks creds + ENABLE_LIVE_TRADING + resolved arm, then EXITS
#     before any broker is built or any swap is signed. rc 0 = OK, rc 2 = NOT ready.
STRATEGY_NAME=momentum_adaptive python scripts/run_allocator.py --mode live --preflight-only
```

`--preflight-only` runs `_live_preflight()`, which fails fast (rc 2, clear reason) on any of: **kill switch
engaged · `TWAK_ACCESS_ID`/`TWAK_HMAC_SECRET` missing · wallet password missing
(`TWAK_WALLET_PASSWORD`/`AGENT_WALLET_PASSWORD`) · `ENABLE_LIVE_TRADING` false.** Expect rc 2 *here* (we
haven't armed yet) citing `ENABLE_LIVE_TRADING is false` — that confirms the only thing missing is the
deliberate flip below.

## Step 2 — Arm (`.env` flip)

Set these keys in `.env` (values are placeholders — never commit real ones; creds are already present in
the operator `.env` from setup):

```dotenv
ENABLE_LIVE_TRADING=true      # master live gate (settings.enable_live_trading)
TWAK_MODE=live                # sign REAL BSC swaps (coupled with the line above)
TRADE_FLOOR_DAILY=true        # arm the >=1-trade/day contest floor (daily_floor.sh no-ops until true)
# MAX_DRAWDOWN_FRAC=0.10      # live DD rail — 10% is the validated campaign rail; widen only eyes-open
# Required already-present (do NOT print/commit): TWAK_ACCESS_ID, TWAK_HMAC_SECRET, TWAK_WALLET_PASSWORD
```

Re-run **1c**: it must now print `preflight-only: LIVE setup OK — would run strategy 'momentum_adaptive'.
No broker built, no swap executed.` (rc 0). That is the green light.

## Step 3 — Install the live crons (contest window only)

The three scripts already self-lock (per-mode Python lock + `flock`), export the node-v26 PATH (TWAK needs
Node ≥ 22), and `PYTHONPATH=src`. Add to `crontab -e` — times are **local IST (UTC+5:30)**:

```cron
# Daily LIVE rebalance tick (13:07 IST ~ 07:37 UTC), day-of-month 22-28 June:
7 13 22-28 6 *   /Users/apple/Desktop/BNB-Hack-CMC/scripts/live_tick.sh
# FAST flatten-only intraday risk monitor (DD halt + profit-lock), every 10 min in-window:
*/10 * 22-28 6 * /Users/apple/Desktop/BNB-Hack-CMC/scripts/dd_watch.sh live
# >=1-trade/day floor near end-of-UTC-day (03:40 IST = 22:10 prev UTC -> dom 23-29):
40 3 23-29 6 *   /Users/apple/Desktop/BNB-Hack-CMC/scripts/daily_floor.sh live
```

Each is one-directional or ~0-NAV by design: `live_tick` rebalances; `dd_watch` only ever flattens (never
opens/flips/rebalances); `daily_floor` banks one ~0-impact round-trip only if the UTC day has zero swaps.
Watch: `tail -f data/logs/allocator_live.log` · `data/logs/dd_watch_live.log` · `data/logs/daily_floor_live.log`.

## Step 4 — Safety recap (already shipped — know where the brakes are)

- **Kill switch (instant halt).** The dashboard "kill" button calls `kill_switch.engage()`, which writes
  the sentinel `data/KILL_SWITCH_ENGAGED` **and** rewrites `ENABLE_LIVE_TRADING=false` in `.env`.
  `_live_preflight()` checks `is_engaged()` first on **every** live entry point (`_tick` / `_dd_watch` /
  `_daily_floor` / `--preflight-only`), so a kill press halts even a long-running process on its next tick.
  Manual engage/release:
  ```bash
  touch data/KILL_SWITCH_ENGAGED        # engage (refuses to trade next tick)
  rm -f data/KILL_SWITCH_ENGAGED        # release sentinel only — does NOT re-enable ENABLE_LIVE_TRADING
  ```
  Releasing the switch is **necessary but not sufficient** to resume — you must also re-set
  `ENABLE_LIVE_TRADING=true` (intentional friction).

- **DD-halt / profit-lock partial-flatten signal.** On a drawdown breach or profit-lock trigger the bot
  flattens token→USDT and halts. The journal records `flattened_ok` vs `flattened_attempted` +
  `flatten_partial`: if `flattened_ok < flattened_attempted`, a sell leg failed and **residual on-chain
  exposure may remain** — reconcile the wallet before resuming.

- **Resume after a halt.**
  ```bash
  python scripts/run_allocator.py --mode live --resume          # clear a DD halt (keeps a profit lock)
  python scripts/run_allocator.py --mode live --resume --force  # ONLY if last flatten was PARTIAL —
                                                                #   acknowledges possible residual exposure
  python scripts/run_allocator.py --mode live --unlock-profit   # deliberately re-open a banked profit-lock
  ```
  Plain `--resume` **refuses** (rc 2) when the last halt's flatten was partial — `--force` is the explicit
  override after you've reconciled the book.

## Step 5 — Roll-back / disarm (after the contest, or to abort)

```bash
crontab -e                              # remove the 3 live cron lines (Step 3)
touch data/KILL_SWITCH_ENGAGED          # belt-and-suspenders: also flips ENABLE_LIVE_TRADING=false in .env
```

Then revert `.env`: `ENABLE_LIVE_TRADING=false`, `TWAK_MODE=sim`, `TRADE_FLOOR_DAILY=false`. Confirm
disarmed with **1c** — it should again report `NOT ready (... ENABLE_LIVE_TRADING is false)`. Pre-contest,
keep the SIM/forward track running (`scripts/forward_tick.sh`, `dd_watch.sh sim`) so the campaign continues
without touching the live balance.

---

### One-screen checklist

| # | Action | Verify |
|---|--------|--------|
| 1 | `make validate_allocator` + `--preflight-only` | bit-for-bit ✅; rc 2 = "ENABLE_LIVE_TRADING false" |
| 2 | `.env`: `ENABLE_LIVE_TRADING=true` · `TWAK_MODE=live` · `TRADE_FLOOR_DAILY=true` | re-run 1c → rc 0 "LIVE setup OK" |
| 3 | Install the 3 live crons (`live_tick` · `dd_watch live` · `daily_floor live`) | `tail -f data/logs/allocator_live.log` |
| 4 | Brakes: kill switch · partial-flatten signal · `--resume [--force]` | know the commands |
| 5 | Disarm: remove crons · kill switch · revert `.env` | 1c → "NOT ready" |
