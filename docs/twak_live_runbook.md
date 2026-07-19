# TWAK Live Runbook — quote-only integration → mainnet flip (contest 2026-06-22)

The agent's execution layer is the Trust Wallet Agent Kit (`twak` CLI) as the **sole signer**.
The `twak` CLI is **mainnet-only** (`twak chains` lists no BSC testnet), so we de-risk before the
contest by running the FULL loop **quote-only** against the real CLI — real on-chain balances and
real router quotes, `execute=False`, nothing signed or spent — then flip a single flag to execute
for the contest.

## ✅ LIVE ARMING — ACTIVE since 2026-06-18 (continuous, pre-contest)

Real BSC spot trading is **armed and running** (`.env`: `TWAK_MODE=live`, `ENABLE_LIVE_TRADING=true`). The
scheduler is **cron** (3 lines below), NOT launchd: launchd LaunchAgents hit macOS **TCC** on the `~/Desktop`
repo (`getcwd: Operation not permitted` → exit 126), while cron already has Full Disk Access (the forward/streamer
crons run). The launchd plists exist under `~/Library/LaunchAgents/com.bnb.*.plist` (unloaded) for the upgrade
path — to use launchd (survives sleep), grant `/bin/bash` **Full Disk Access** in System Settings → Privacy, then
`launchctl load -w` them.

```
*/5 * * * *  scripts/cmc_stream.sh       # CMC 4h streamer watchdog (the live arm needs the feed)
15 18 * * *  scripts/live_tick.sh        # daily LIVE rebalance — REAL swaps (18:15 IST ≈ 12:45 UTC)
*/30 * * * * scripts/dd_watch.sh live    # 30-min drawdown watch (flatten-only)
```

**Proven 2026-06-18:** a manual `live_tick.sh` ran the full live path (real ERC-8004 heartbeat tx `0x1aab75f4…`,
REBALANCE journaled `mode:live`; 0 swaps that tick because the book already matched the momentum target — it swaps
when the target shifts, as the 06-18 test's 3 swaps showed).

**Brakes:** kill switch — `python -c "from ictbot.runtime.kill_switch import engage; engage('reason')"` (stops
instantly + flips `ENABLE_LIVE_TRADING=false`); auto dd-halt flattens at 10%; stale-candle (>12h) skip; dust guards.
**Sleep caveat:** cron pauses while the Mac sleeps — keep it awake (`caffeinate`/Energy-Saver) or move to launchd+FDA.
**06-22:** still re-anchor — `run_allocator --mode live --anchor-nav <open NAV>` (the in-window arm_check gate enforces it).

## What runs in each mode

| | client | swaps | journal / state | creds / funds |
|---|---|---|---|---|
| `--mode sim` (default) | `SimTwakClient` (paper) | simulated fills | `allocator_journal.jsonl` / `allocator_state.json` | none |
| `--quote-only` (integration) | `CliTwakClient` (real CLI) | **real router quotes, execute=False** | `allocator_dryrun.jsonl` / `allocator_dryrun_state.json` | none — read-only on-chain |
| `--mode live` (contest) | `CliTwakClient` (real CLI) | **signed BSC swaps** | `allocator_live.jsonl` / `allocator_live_state.json` | creds + wallet + `ENABLE_LIVE_TRADING=true` |

The three namespaces are fully isolated — a dry-run can never touch the contest's `allocator_live.*`.

## Prerequisite: resolve the `twak` binary (REQUIRED for cron)

`twak` is an npm global (here under nvm) and is NOT on a minimal cron PATH. Set the absolute path:

```
TWAK_BINARY=/Users/apple/.nvm/versions/node/v26.3.0/bin/twak   # in .env
```

`CliTwakClient` also prepends this binary's directory to the subprocess PATH, so twak's
`#!/usr/bin/env node` shebang finds the co-located `node` even with an empty PATH. Verified: with a
stripped PATH and no shell export, `CliTwakClient().balance("USDT")` returns the real on-chain balance.

## Integration phase (now → 06-21): quote-only

```
# one quote-only tick (full loop vs the real CLI; nothing signed)
PYTHONPATH=src python scripts/run_allocator.py --quote-only --dd-cap 0.10
```

Proven 2026-06-16 on the funded wallet `0xE8A30d24…BbA…6215` (NAV ~$8.20):
- Real on-chain balances read; regime computed (F&G=25 → cap 0.51); 8-token target produced.
- **5 real router quotes** flowed broker → `CliTwakClient` → `twak swap --quote-only` from 2 live
  aggregators (`0x`, `LiquidMesh`); `n_failed=0`. `tx` holds **provider tags, not hashes** (no fill).
- Journaled to `allocator_dryrun.jsonl` as `REBALANCE_DRYRUN` (`dry_run:true`); x402 + heartbeat
  suppressed (no real spend / on-chain write); contest `allocator_live.*` untouched.

### Small-bankroll note (important)
At a small NAV, `1/k`-weight positions fall under the `$1` `min_swap_usd` dust floor and the agent
skips EVERY swap. Lower the floors for a small contest book:
```
ALLOC_MIN_SWAP_USD=0.5        # or lower
ALLOC_MIN_REBAL_FRAC=0.01
```

### Guardrails verified on the live(quote-only) path (2026-06-16)
- **Kill switch:** `kill_switch.engage()` → tick prints `KILL SWITCH ENGAGED — refusing to trade`.
- **Drawdown halt:** with a seeded peak HWM, dd=31.6% > 10% cap → `DD_HALT` + `emergency_flatten`
  (quote-only sells of both held positions, `flatten_partial:false`).
- **Token allowlist:** structural — the universe is `CONTEST_TOKENS`; deselecting a held token sells
  it toward 0 next tick (demonstrate at flip with a real fill).
- **Slippage:** `--slippage <TWAK_SLIPPAGE_PCT>` is appended only on an EXECUTE; demonstrate the
  breach path at the mainnet flip (a quote has no fill to slip).

## Mainnet flip for the contest (2026-06-22)

Config diff (quote-only → live). Everything else (creds, `AGENT_ID=133085`, x402 on Base, cron,
guardrails) is unchanged:

```
TWAK_MODE=live
ENABLE_LIVE_TRADING=true
# TWAK_CHAIN=bsc            # default; mainnet
# AGENT_NETWORK=bsc         # default; mainnet identity
ALLOC_MIN_SWAP_USD=0.5      # match the actual bankroll (see note above)
```

Then drop `--quote-only` — the SAME loop now signs:

```
# ── MANDATORY FINAL GATE — run LAST, immediately before arming (rc 0 ⇒ safe). ──
# Proves REAL, FRESH CMC data is live and feeding the arm (live cmc_price for every token, the
# partial 4h bar on the current bucket, WS quote freshness, F&G/regime reach) AND creds + gas +
# the zero-CEX firewall. See docs/pretrade_cmc_data_audit.md.
make arm_check                 # expect: ✓ READY — all checks green
# preflight (creds + ENABLE_LIVE_TRADING + resolved strategy; no swap)
PYTHONPATH=src python scripts/run_allocator.py --mode live --preflight-only
# arm the daily tick + intraday DD watch via cron (note the explicit nvm/PATH)
10 0 * * *  cd <repo> && . .venv/bin/activate && PYTHONPATH=src python scripts/run_allocator.py --mode live --dd-cap 0.10 >> data/logs/allocator_live.log 2>&1
*/30 * * * * cd <repo> && . .venv/bin/activate && PYTHONPATH=src python scripts/run_allocator.py --mode live --dd-watch --dd-cap 0.10 >> data/logs/allocator_live.log 2>&1
```

Checklist:
1. `TWAK_BINARY` set to the absolute path (cron resolution).
2. Fund the trading wallet `0xE8A30d24…6215` (USDT + a little BNB for gas). *(manual)*
3. `CONTEST_START=2026-06-22` / `CONTEST_END` at real values (undo any Phase-2 drill bracketing).
4. No stale `allocator_live_state.json` (absent today → first live tick re-seeds HWM from on-chain NAV).
   `PROFIT_LOCK_ENABLED=1` is set — use `--anchor-nav` if relying on profit-lock.
5. Kill switch released; `ENABLE_LIVE_TRADING=true` restored after any drill.
6. `--preflight-only` green.
7. **`make arm_check` → ✓ READY** — THE mandatory final gate: real CMC data is live and feeding the
   arm (CMC-data-liveness probes) + creds + gas + firewall. See `docs/pretrade_cmc_data_audit.md`.

## Open items (tracked separately)
- **Docs coherence:** done — x402 counts trued-up to 20+/$0.20+ in `SUBMISSION.md` / docs.
- **x402 breadth (stretch):** add a second paid surface (`quotes_latest` per tick) beyond `dex_search`.

---

# ALL-PILLARS MAINNET MVP — armed for 2026-06-22

The four integrations as one coherent mainnet MVP. **Armed, not live**: everything is wired +
validated; no real swap fires until the 06-22 flip. Status today (2026-06-16):

| Pillar | Mainnet status | What it needs to go live |
|---|---|---|
| **x402 (CMC data, Base)** | ✅ LIVE — 21 settled, $0.21 | nothing (Base USDC already funded) |
| **TWAK (trading, BSC)** | ✅ armed — proven quote-only | the 06-22 `.env` flip + trading-wallet funds |
| **ERC-8004 (identity heartbeat)** | ⚠️ wired + verifiable — **fixed** (below) | fund identity wallet ~0.002 BNB (direct-gas) |
| **ERC-8183 (commerce)** | ✅ testnet-proven, mainnet-ready | `ERC8183_NETWORK=bsc-mainnet` + mainnet "U" (optional) |

## The broken ERC-8004 heartbeat wiring — FIXED (2026-06-16)
Heartbeats silently never landed (gasless 403 + the direct-gas identity wallet ≈ 0 BNB, and
`write_heartbeat` swallowed the reason). Now:
- `write_heartbeat` returns `{ok, tx?, error?}` and **logs the real reason** (no silent swallow); the
  tick journals it (`heartbeat` field) → dashboard surfaces it (IdentityCard "heartbeat" line).
- `read_heartbeat()` reads the on-chain blob back — **verification** (proven against 133085, which
  already holds a real heartbeat from 2026-06-14).
- `make heartbeat_check` reports the funding path **actionably** (e.g. "fund 0xEb7b… with ≥ 0.001 BNB
  (have 0.000004)") instead of failing silently.
- **Unblock:** fund identity wallet `0xEb7bF36aab4912c955474206EF0b835170389655` with ~0.002 BNB
  (direct-gas, current config) — OR set `AGENT_USE_PAYMASTER=true` + provision the MegaFuel sponsor
  policy on NodeReal (gasless). Then heartbeats land + `make heartbeat_check` shows `ready:true`.

## Unified fund table (the only manual steps)
| Wallet | Asset | Amount | For |
|---|---|---|---|
| Trading `0xE8A30d24…6215` | USDT + BNB | ~$10 + ~0.005 BNB | TWAK swaps + gas |
| Identity `0xEb7b…9655` | BNB | ~0.002 | ERC-8004 heartbeat gas (direct-gas) |
| Identity `0xEb7b…9655` | Base USDC | already funded | x402 ($0.01/call) |
| (optional) ERC-8183 buyer | mainnet "U" | small | commerce escrow (only if `ERC8183_NETWORK=bsc-mainnet`) |

## The 06-22 contest open — RE-ANCHOR + window cron (NOT a first arming)
Live trading is **already armed and running since 06-18** (see the top section). The `.env` flags below
are **already set** — confirm them, do NOT re-flip. The only NEW actions at the window open are: (1)
**re-anchor** the PnL baseline to the on-chain opening NAV, and (2) **swap** the always-on pre-contest
cron for the window-scoped (days 22–28) contest cron.
```
# .env — already LIVE since 06-18; confirm (do not re-flip):
TWAK_MODE=live
ENABLE_LIVE_TRADING=true
ALLOC_MIN_SWAP_USD=0.5
DASHBOARD_JOURNAL=live
# AGENT_HEARTBEAT_ENABLED=true, AGENT_ID=133085 already set; x402 already live.

# RE-ANCHOR the PnL baseline to the on-chain OPENING NAV (else the contest return is measured from the
# pre-contest test NAV; arm_check's 'profit-lock anchor freshness' row FAILs in-window until you do this):
PYTHONPATH=src python scripts/run_allocator.py --mode live --anchor-nav <on-chain NAV at the 06-22 open>
make arm_check                                                                # MANDATORY FINAL GATE: ✓ READY (anchor fresh + real CMC data live + creds, gas, firewall) — docs/pretrade_cmc_data_audit.md
PYTHONPATH=src python scripts/run_allocator.py --mode live --preflight-only   # must be green
make heartbeat_check                                                          # ready:true once funded
# contest-week cron (days 22–28 only; REMOVE after 06-28):
7 13 22-28 6 *   <repo>/scripts/live_tick.sh
*/30 * 22-28 6 * <repo>/scripts/dd_watch.sh
0 14 24-28 6 *   <repo>/scripts/settle_commerce.sh   # finalize served ERC-8183 jobs (dispute window ≈06-24)
make deploy_dashboard                                                         # flip dashboard to live
```
⚠️ **`settle_commerce.sh` needs the BUYER keystore creds persisted.** The two mainnet jobs were created
with `CLIENT_WALLET_PASSWORD` / `CLIENT_WALLET_DIR` passed *inline* — they are NOT in `.env`, so the cron
settle no-ops (`buyer_available()=False`, logged rc=2) until you add them to `.env` (gitignored) or
`$REPO/.env.commerce.local` (gitignored `*.local`). Confirm once by hand: `bash scripts/settle_commerce.sh`
→ `tail data/logs/settle_commerce.log` should show the job ids, not "buyer-side commerce unavailable".
**Single scheduler — cron only.** The trading launchd plists (`com.bnb.live_tick`, `dd_watch`, `cmc_stream`)
must stay **UNLOADED** (only `com.bnb.caffeinate` is loaded, the keep-awake) — do NOT `launchctl load` them
while cron is active, or the live tick fires twice. They exist solely as the FDA-path fallback (see the top
section). Verify: `launchctl list | grep com.bnb` → only `caffeinate`.

**Strategy auto-selector (recommend-only for live).** `scripts/strategy_evaluator.sh` (cron `50 6,18`,
just after the forward sweep) ranks DQ-safe arms by risk-adjusted forward score with anti-chasing
hysteresis, auto-drives the SIM book, and writes a LIVE recommendation to the dashboard's Auto-selector
card. It NEVER changes the real-money arm — apply a recommendation with `live_tick.sh <arm>` (or flip the
default-OFF `STRATEGY_AUTO_APPLY_LIVE`). Details: [docs/strategy_autoselector.md](strategy_autoselector.md).

**Live dashboard auto-refresh (push).** After each rebalance, `live_tick.sh` runs `scripts/publish_snapshot.sh`
→ regenerates the LIVE snapshot + POSTs it to the deployed API's `POST /api/ingest/snapshot` (token-gated by
`INGEST_TOKEN`), so the public dashboard reflects the tick within ~4s — NO git push / image rebuild. `dd_watch.sh
live` re-pushes every 30 min to keep the override warm across free-tier cold-starts. Setup: same `INGEST_TOKEN`
in `.env` (local) AND on Render (see [docs/deploy_dashboard.md](deploy_dashboard.md) §3). It's best-effort —
a failed push never affects the trade. Verify: `tail data/logs/publish_snapshot.log` → `http=200`.

## Validate NOW (no funds, no live trade)
```
PYTHONPATH=src python -m pytest -q                                            # full suite green
ENABLE_LIVE_TRADING=true TWAK_MODE=live python scripts/run_allocator.py --mode live --preflight-only
python scripts/run_allocator.py --quote-only                                 # real router quotes, execute=False
make heartbeat_check                                                         # actionable readiness + on-chain read-back
```

## ERC-8183 commerce on mainnet (optional)
`ERC8183_NETWORK=bsc-mainnet` (code already routes mainnet → keyed paymaster via `commerce._network`);
the buyer funds mainnet "U" (`0xcE24439F2D9C6a2289F741120FE202248B666666`). Default stays bsc-testnet
(free, proven). See `docs/erc8183_agent_commerce.md`.

## Pre-fund readiness — verified 2026-06-16

**Go-live in 3 steps (on 2026-06-22): fund 2 wallets → flip 2 flags → arm cron.** Everything else is
wired, tested (1621 green), deployed, and dashboard-verified (CMC rotation live; no-"Binance" gate
green; `make verify_dashboard`). A pre-fund sweep — every check possible **without funds** (read-only /
`execute=False`, zero spend) — confirms the ONLY remaining blockers are money + the `.env` flip:

| Check | Command | Verdict (2026-06-16) |
|---|---|---|
| TWAK live preflight | `run_allocator --mode live --preflight-only` | ✅ creds + wallet password + kill-switch + strategy `momentum_cmc` all pass — fails ONLY on `ENABLE_LIVE_TRADING=false` (the intended flip) |
| Quote-only full loop | `run_allocator --mode live --quote-only` | ✅ full loop on real on-chain NAV $8.15 → real CMC decision → `execute=False`, exit 0, 0 failures (router-quote path proven 06-16) |
| ERC-8004 heartbeat | `make heartbeat_check` | ✅ on-chain read-back PROVEN (real heartbeat 06-14) · ⛽ NOT READY — fund identity `0xEb7b…9655` ≥ 0.002 BNB (direct-gas) |
| x402 (CMC data, Base) | `data/x402/receipts.json` | ✅ LIVE — 22 settled on Base (`eip155:8453`) |
| ERC-8183 commerce | testnet-proven | ✅ mainnet-ready via `ERC8183_NETWORK=bsc-mainnet` (optional sell-side) |

**Heartbeat gas note:** MegaFuel gasless sponsorship returns 403 without `NODEREAL_API_KEY` (not
deployed), so the heartbeat go-live path is **direct-gas** — fund the identity wallet ~0.002 BNB (per the
fund table). The on-chain *read-back* already proves a heartbeat landed; funding lets the per-tick writes fire.

No real swap / per-tick heartbeat / mainnet ERC-8183 settle has fired — **by design** (disarmed). Funding
the two wallets + the flip (`TWAK_MODE=live`, `ENABLE_LIVE_TRADING=true`) is exactly what lets those final
txs run. See the **Unified fund table** + **The 06-22 go-live** sections above.
