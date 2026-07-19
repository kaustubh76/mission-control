# Scalability Architecture — Mission Control

> How this production build scales to many concurrent users, why we chose the axes we did,
> when (and whether) a load balancer is needed, and the exact levers to pull as load grows.
>
> **Status:** read-tier cache layer is **active by default**; multi-instance / Redis / load-balancer
> paths are **implemented and gated off** (free-tier-safe defaults). Going to paid multi-instance is a
> config flip, not a code change.

---

## 1. TL;DR

This is **not a monolith with one scaling axis.** It is a **two-tier system whose tiers scale in
opposite directions**:

| Tier | What it is | Scaling axis | One-line reason |
|---|---|---|---|
| **Read** | Dashboard API (`src/ictbot/api/app.py`) + the Vercel SPA | **Horizontal** — cache-first, then replicas | One identical global snapshot for every viewer → cacheable; replicas are trivial once state is shared |
| **Write** | The trading allocator (`scripts/run_allocator.py`) | **Vertical + active-passive only — never replicated** | Single wallet, single high-water-mark, single ledger; two writers double-trade |

**The single highest-leverage move for "real users" is a short edge cache on `/api/snapshot`, not a
load balancer.** Because every viewer fetches the *same* global object, a 3-second shared-edge cache
collapses N viewers × (1 poll / 4s) into **~1 origin request per 3-second window — regardless of how
many viewers there are.** A load balancer is reached for *availability*, much later than for throughput.

---

## 2. Current topology

```
                         many concurrent viewers
                                   │  poll GET /api/snapshot every 4s
                                   ▼
                    ┌──────────────────────────────┐
                    │  Vercel (global CDN edge)     │   web/  — React/Vite SPA
                    │  • serves the static SPA      │   bnb-mission-control-two.vercel.app
                    │  • /api/* reverse-proxied ────┼──┐ edge-caches /api/snapshot per s-maxage
                    └──────────────────────────────┘  │ (web/vercel.json + deploy_dashboard.sh)
                                                       ▼
                                   ┌────────────────────────────────┐
                                   │  Render web service (FastAPI)   │  infra/Dockerfile.dashboard
                                   │  READ-ONLY: /api/snapshot, …    │  bnb-mission-control-api.onrender.com
                                   │  • edge cache headers + ETag    │  src/ictbot/api/app.py middleware
                                   │  • in-process micro-cache       │  reads.snapshot_cached()
                                   │  • pushed_snapshot store ◄──────┼── token-gated POST /api/ingest/snapshot
                                   └────────────────────────────────┘            ▲
                                                                                  │ best-effort push after each tick
                    ┌──────────────────────────────────────────────┐            │ scripts/publish_snapshot.sh
                    │  Local operator machine (SINGLE writer)       │────────────┘
                    │  cron: live_tick.sh / forward_tick.sh /       │
                    │        dd_watch.sh / cmc_stream.sh            │  scripts/run_allocator.py
                    │  state: data/journal/allocator_*_state.json   │  (fcntl.flock per mode)
                    │  signing keys + twak CLI live ONLY here       │
                    └──────────────────────────────────────────────┘
```

- The **allocator runs on the operator machine**, not on Render. It rebalances, writes the journal,
  then *pushes* a fresh snapshot to the read API so the public dashboard reflects it within one poll.
- Render hosts a **read-only** API. It holds no signing keys (free tier has no account 2FA — see
  `render.yaml` security header).
- The SPA is static and already globally CDN-distributed by Vercel (infinitely scalable for assets).

---

## 3. The scaling decision, per tier

### Read tier → horizontal (cache-first, then replicas)
Each request is stateless: it reads the pushed snapshot (a single small object) or rebuilds one from
the journal. Every viewer gets the **same** payload, so:
1. a **shared edge cache** serves nearly all reads without touching the origin, and
2. once the pushed snapshot lives in **shared storage**, you can run **N identical replicas** behind a
   load balancer with no coordination.

### Write tier → vertical + active-passive only (**`numInstances` = 1, forever**)
The allocator is a **single-writer system by construction**:
- It tracks the **high-water mark, halt flag, paper balances, and trade-floor cursor** in
  `data/journal/allocator_*_state.json` (atomic `tmp` + `os.replace` writes).
- It appends one row per tick to `data/journal/allocator_*.jsonl`.
- It rebalances **one wallet** on-chain via the `twak` CLI.

Run it active-active and two instances both read a stale HWM, both compute allocations, and both
execute swaps → **double trades + a desynced ledger + a corrupted drawdown halt.** No cache or lock
makes two-writers-on-one-wallet correct. Therefore the allocator scales **only** by giving its single
instance more CPU/RAM (it needs very little) and, for availability, an **active-passive standby** that
stays cold. It is **never** placed behind the multi-instance load balancer.

Guarantees in code:
- `run_allocator._acquire_lock()` — `fcntl.flock(LOCK_EX|LOCK_NB)` per mode → two ticks on one host
  can't overlap (cron overlap + a manual run, or a slow tick when the next cron fires).
- `run_allocator._acquire_redis_lease()` — **optional** cross-host backstop (`ALLOCATOR_LOCK=redis`):
  a `SET … NX PX` lease so an *accidental* second host can't tick either. Fail-closed: if the lease
  store is unreachable while enabled, the tick **skips** rather than risk an unguarded write.

---

## 4. Cache-first read strategy (active by default)

This is the work that makes "real users data" scale. Four layers, outermost first:

**(A1) Edge / CDN cache headers** — `src/ictbot/api/app.py` `_edge_cache` middleware.
Every `GET /api/*` (except writes and the live `/api/agent-hub/ping` probe) gets:
```
Cache-Control:            public, max-age=0, s-maxage=3, stale-while-revalidate=27
CDN-Cache-Control:        public, s-maxage=3, stale-while-revalidate=27
Vercel-CDN-Cache-Control: public, s-maxage=3, stale-while-revalidate=27
```
- `s-maxage=3` → the shared edge serves a cached copy for 3s ⇒ **at most ~1 origin fetch / 3s for
  `/api/snapshot`, for any number of viewers.**
- `stale-while-revalidate=27` → the edge serves the last-good copy *instantly* while refreshing in the
  background, so a Render cold-start (~30s wake) never shows a blank dashboard.
- `max-age=0` keeps the *browser* honest (always revalidate) while `s-maxage` governs the shared edge.
- Per-path overrides (`_CACHE_TTL_OVERRIDES`): slower-moving sections cache longer
  (`/api/market-intel`=30s, `/api/cmc-api`=15s). Override the base with `EDGE_SMAXAGE_S`; `0` disables.

> **This needs a shared edge in front of Render.** Render's web service has no built-in CDN, so the
> SPA is served **same-origin** and Vercel reverse-proxies `/api/*` to Render — Vercel's edge then
> honors `s-maxage`. The route is baked into the prebuilt Build Output by `scripts/deploy_dashboard.sh`
> (`web/vercel.json` documents the same rewrite for non-prebuilt deploys). This also **removes CORS**
> (same-origin). If Vercel ever stops caching the external proxy, front Render with **Cloudflare**
> instead — no application change. `web/public/config.json` `apiBase:""` selects same-origin; reverting
> it to the Render URL bypasses the edge proxy.

**(A2) In-process micro-cache** — `reads.snapshot_cached()` (TTL `SNAPSHOT_CACHE_TTL_S`, default 2s).
The pushed snapshot is a cheap single read and is served first; this protects only the **heavy
journal-rebuild fallback** (cold edge / SWR refresh / no-CDN case), which can be slow (per-card reads).
It is **single-flight + serve-stale**: a burst of cache-miss requests triggers **one** rebuild while
the others serve the last-good value — so concurrent cold-cache viewers can't melt the free-tier CPU.

**(A3) Ingest invalidation** — `ingest.py` calls `reads.invalidate_snapshot_cache()` right after a
push, so the next rebuild reflects fresh data immediately. The edge picks it up within `s-maxage` (≤3s)
— no active purge (keeps the best-effort push path failure-free).

**(A4) ETag / 304** — the middleware emits a weak ETag (body hash) on `/api/snapshot` and returns
**304 Not Modified** when the client's `If-None-Match` matches, saving the ~50–200 KB re-download. The
SPA poll uses `fetch(…, {cache:"no-cache"})` (`web/src/api/client.ts`) so the browser participates in
revalidation; diagnostic self-test GETs stay `no-store` to report the truth about origin reachability.

---

## 5. Shared-state externalization (enables read replicas)

The only thing blocking a second Render replica is that the pushed snapshot is, by default, a
**per-instance local file** (`data/_pushed_snapshot.json`): an ingest POST lands on **one** replica, so
the others would serve the baked seed. The store is now **pluggable** (`reads.store_pushed_snapshot` /
`reads.pushed_snapshot`):

| `SNAPSHOT_STORE` | Backend | Use |
|---|---|---|
| `file` *(default)* | Atomic local file | Single instance — the contest deploy. Zero dependencies. |
| `redis` | Shared key (`ictbot:pushed_snapshot`, `SET … EX`) via `REDIS_URL` | **Multi-instance** — all replicas read the same pushed snapshot. |

- The Redis client (`reads._redis()`) is **lazily imported** — `redis` ships only in the optional
  `[scale]` extra (`pip install -e ".[api,scale]"`), so the default image stays lean.
- Any Redis error **degrades to None** (read) / **falls through to the file** (write) — the read
  surface never breaks because a cache is down, and a push is never silently lost.
- Belt-and-suspenders TTL: the existing `served_at` freshness check **plus** Redis `EX`.

Config flags (all default to today's behavior): `SNAPSHOT_STORE=file`, `REDIS_URL=`,
`SNAPSHOT_CACHE_TTL_S=2`, `EDGE_SMAXAGE_S=3`. Documented (commented) in `render.yaml`.

---

## 6. Write-tier hardening

| Guard | Scope | Default |
|---|---|---|
| `fcntl.flock` per mode (`_acquire_lock`) | Same host | **On** (existing) |
| Redis lease `SET … NX PX` (`_acquire_redis_lease`) | Cross host | Off (`ALLOCATOR_LOCK=flock`); opt-in `=redis` |

**Durability of the single writer:**
- **Contest week (active):** keep the **local cron**. It already holds the signing keys + `twak`
  toolchain, is already locked, and `publish_snapshot.sh` keeps the cloud dashboard live. Harden the
  machine for 2026-06-22 → 06-28 (awake, on power, wired). The daily cadence makes one missed tick
  recoverable; `dd_watch.sh` (every 10 min) bounds intraday risk.
- **Post-contest (roadmap):** move the allocator to a **single Render cronjob/worker**
  (`numInstances: 1` forever) running `run_allocator.py --mode live --loop` with `ALLOCATOR_LOCK=redis`.
  Caveats: Render's IP can't reach Binance (the live arm is `CMC_ONLY=true`, so CMC-native only), and
  signing secrets may live there **only on a paid, 2FA-protected account** (`render.yaml` forbids
  secrets on the no-2FA free tier).

**Invariant:** the allocator is never a load-balanced web service. If it ever runs on Render it is a
separate worker with `numInstances: 1`.

---

## 7. Load balancer & horizontal-scaling triggers

**You do not build a load balancer.** Render auto-provisions a managed LB the instant a web service's
instance count goes > 1 (Standard plan+). Vercel already load-balances the SPA globally. The cache-first
strategy defers the LB substantially — it's reached for **availability/HA** long before throughput.

| Trigger / metric | Threshold | Action |
|---|---|---|
| Concurrent viewers | < ~500 | Free tier + keep-alive; no change |
| Concurrent viewers | 500 – 5k | The **edge cache (§4) alone covers it** — verify it's honored |
| Origin req/s to `/api/snapshot` | sustained > 5/s **after** the edge | Investigate cache miss-rate / `s-maxage` *before* scaling |
| Render instance CPU | > 70% sustained | Render Standard, `numInstances: 2` (LB auto-attaches) + `SNAPSHOT_STORE=redis` |
| Need zero-downtime deploy / HA | any | `numInstances: 2` — **requires the Redis store (§5) first** |
| Free-tier cold-starts visible to users | recurring | Paid (no sleep) **or** rely on `stale-while-revalidate` to mask wake |
| p95 `/api/snapshot` latency | > 500 ms | Confirm the micro-cache is on; then add replicas |
| Allocator (write tier) | **any load** | **Never scale. Vertical only + active-passive.** |

---

## 8. Phased roadmap

**Phase 0 — contest-safe, active now (low risk, additive):**
- Edge cache headers + ETag/304 (A1/A4), in-process micro-cache + invalidation (A2/A3).
- Vercel same-origin `/api/*` edge proxy (A5) — gives the headers a real edge and removes CORS.
- Singleton story documented; local cron hardened.

**Phase 1 — scale-out levers (implemented, flip when triggered):**
- `SNAPSHOT_STORE=redis` + `REDIS_URL` (shared snapshot) → unlocks `numInstances: 2`.
- Render Standard plan + persistent disk for a continuously-growing journal.
- `ALLOCATOR_LOCK=redis` if the allocator ever leaves the single host.

**Phase 2 — production HA:**
- Multi-replica read API behind Render's LB; paid, 2FA-protected account.
- Allocator as a single Render worker (`--loop`, `numInstances: 1`) with active-passive standby.
- Cloudflare in front of Render if a guaranteed edge cache is required.

---

## 9. Capacity & cost

**Origin request math** (edge `s-maxage=3`, SPA polls every 4s). With a shared edge, origin load is
**independent of viewer count** — it's `1 / s-maxage` for the snapshot:

| Viewers | Polls/s hitting the **edge** | Origin req/s for `/api/snapshot` (with edge) | Without any edge |
|---|---|---|---|
| 100 | ~25 | ~0.33 | ~25 |
| 1,000 | ~250 | ~0.33 | ~250 |
| 10,000 | ~2,500 | ~0.33 | ~2,500 |

A single small Render instance handles ~0.33 origin req/s trivially; **the edge cache is what makes
10k viewers a non-event.** Without it, 10k viewers would be ~2,500 req/s of journal rebuilds — far past
free tier. (The micro-cache caps even the no-edge case at one rebuild per `SNAPSHOT_CACHE_TTL_S`.)

**Cost:**
| Item | Free path (now) | Paid path (Phase 1+) |
|---|---|---|
| Render web API | Free (sleeps 15m idle; keep-alive pings) | Standard ~$7–25/mo (no sleep, multi-instance, disk) |
| Shared snapshot store | n/a (file) | Upstash Redis free tier (≥10k cmds/day) → ~$0–10/mo |
| Vercel SPA + edge | Free (Hobby) | Pro if traffic/limits require |
| Bandwidth | ~100 KB/snapshot; ETag/304 cuts repeat polls to headers-only | — |

---

## 10. Runbook

- **Enable the edge:** ship via `make deploy_dashboard` (bakes the `/api/*` proxy route + same-origin
  `config.json`). **Not** `vercel --prod` (the project's framework preset rejects it).
- **Verify headers:** `curl -sI <api>/api/snapshot | grep -i 'cache-control\|etag'`.
- **Scale reads out:** set `SNAPSHOT_STORE=redis` + `REDIS_URL` (out-of-band, `sync:false`), upgrade to
  Standard, set `numInstances: 2` in `render.yaml`. The LB attaches automatically.
- **Confirm the allocator is still a singleton:** launch two ticks — the second must print
  `SKIP … lock held` (rc=2). For cross-host: `ALLOCATOR_LOCK=redis` and check `redis-cli GET
  allocator_lock:<mode>`.
- **Cold-start during the contest:** SWR serves the last-good snapshot during the ~30s wake; the
  keep-alive ping (GitHub Actions / UptimeRobot) on `/api/health` minimizes sleeps.
- **Rollback the edge proxy:** set `web/public/config.json` `apiBase` back to the Render URL and
  redeploy → the SPA hits Render directly (CORS is still configured in `render.yaml`).
- **Disable a cache layer:** `EDGE_SMAXAGE_S=0` (no edge headers) or `SNAPSHOT_CACHE_TTL_S=0` (no
  micro-cache) — both fully reversible via env.

---

## 11. Verification

```bash
# 1. Cache headers present on reads, absent on writes / the live probe
curl -sI <api>/api/snapshot | grep -i 'cache-control\|etag'      # s-maxage=3, stale-while-revalidate=27, ETag
curl -sI <api>/api/agent-hub/ping | grep -i cache-control || echo "ping not cached (correct)"

# 2. ETag / 304 round-trip
ET=$(curl -sI <api>/api/snapshot | awk -F'"' '/[Ee]tag/{print $2}')
curl -sI -H "If-None-Match: \"$ET\"" <api>/api/snapshot | head -1   # → HTTP/.. 304

# 3. Edge collapse (behind Vercel/Cloudflare): two reads inside the window, 2nd served from edge
curl -sI <edge-url>/api/snapshot; sleep 1; curl -sI <edge-url>/api/snapshot | grep -i age

# 4. Load test the read tier (read-only)
hey -z 30s -c 50 <api>/api/snapshot     # p95 stable; with edge, origin req ≈ duration / s-maxage

# 5. Allocator stays singleton
python scripts/run_allocator.py --mode sim &     # 2nd must SKIP (rc=2), never double-trade
python scripts/run_allocator.py --mode sim

# 6. The automated suite
pytest -q tests/test_scaling.py                  # cache headers, micro-cache, store, lease
ruff check src tests && ruff format --check src tests
```
