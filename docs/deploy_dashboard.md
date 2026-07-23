# Deploy the Mission Control dashboard

> **Current deployment (2026-07):** API = Render web service **`mission-control-api`** →
> <https://mission-control-api-iez5.onrender.com> (created via the Render API from the public repo
> **github.com/kaustubh76/mission-control**); UI = Render **static site** `mission-control-vlayer` →
> <https://mission-control-vlayer.onrender.com>. The Vercel + Render Blueprint flow below is the
> original approach, kept for reference.

Two pieces, already split:

```
  Vercel (React SPA)  ──fetch /api/*──▶  Render (read-only FastAPI)
  bnb-mission-control-two.vercel.app      bnb-mission-control-api.onrender.com
```

- **UI** — the React SPA in [`web/`](../web), live on Vercel:
  <https://bnb-mission-control-two.vercel.app>
- **API** — the lean read-only FastAPI on Render, surfacing all three Track-1
  pillars (CMC/x402 · TWAK · BNB-SDK/NodeReal).

This is the BNB-contest dashboard — **separate from the legacy ICT scanner**
(`./Dockerfile`). The Render image is
**API-only and lean**: no SPA build, and no Streamlit/Plotly (those are the
opt-in `[ui]` extra now, not core deps).

- Blueprint: [`render.yaml`](../render.yaml)
- Image: [`infra/Dockerfile.dashboard`](../infra/Dockerfile.dashboard) — `pip install .[api,bnb]`, `CMD ictbot-api`

---

## Part A — Render API

### 1. Push

```bash
git add render.yaml infra/Dockerfile.dashboard .dockerignore pyproject.toml \
        web/public/config.json docs/deploy_dashboard.md
git commit -m "deploy(dashboard): lean API-only Render blueprint; SPA stays on Vercel"
git push
```

### 2. Create the Blueprint

[Render Dashboard](https://dashboard.render.com) → **New +** → **Blueprint** →
connect this repo. Render detects [`render.yaml`](../render.yaml) and proposes
**`bnb-mission-control-api`** (Docker, free). **Apply**. First build ~2–3 min.

### 3. Secrets — exactly ONE low-value token (near-zero-secret deploy)

**Render free tier has no account 2FA**, so we set **no funds-bearing secret** — nothing
that can sign or move money if the account is ever compromised. Every other var is a
committed non-secret in `render.yaml` (`AGENT_NETWORK=bsc`, the **public**
`AGENT_IDENTITY_ADDRESS` / `AGENT_TRADING_ADDRESS`, `DASHBOARD_JOURNAL=live`,
`API_SEED_ON_START=1`, the Vercel CORS origin).

The **one** secret to set is `INGEST_TOKEN` — it gates the live-refresh write endpoint
`/api/ingest/snapshot` (the local tick PUSHes a fresh snapshot here after each rebalance so
the dashboard updates within ~4s, no redeploy). It's **low-value**: a leak at worst lets
someone spoof the PUBLIC dashboard JSON — it can't sign, move funds, or read a key. Set the
SAME value locally (`.env`) and on Render. Empty on the server ⇒ the endpoint 503s (disabled).

```bash
# set INGEST_TOKEN on the API service via the Render REST API (key from ~/.render/cli.yaml):
curl -fsS -X PUT "https://api.render.com/v1/services/srv-d8k4ukv7f7vs7394rmkg/env-vars/INGEST_TOKEN" \
  -H "Authorization: Bearer $RENDER_API_KEY" -H "Content-Type: application/json" \
  -d "{\"value\":\"$(grep '^INGEST_TOKEN=' .env | cut -d= -f2-)\"}"   # triggers one redeploy
```

> 🔒 **Never set on Render:** `AGENT_PRIVATE_KEY`, `AGENT_WALLET_PASSWORD`, any `TWAK_*`,
> and (without 2FA) not even `CMC_API_KEY` / `NODEREAL_API_KEY`. All keys stay local —
> only the low-value `INGEST_TOKEN` lives on the deploy.

**Optional (only if you accept the no-2FA risk):**

| Key | Risk | Adds |
|---|---|---|
| `CMC_API_KEY` | low — rate-limited data key | live Fear & Greed (else from snapshot) |
| `NODEREAL_API_KEY` | bounded — leak can spend the MegaFuel gas tank up to your **sponsor-policy cap** | the live `sponsorable` check (else shown key-free; verify with `make verify_nodereal`) |

### 4. Verify

```bash
curl https://bnb-mission-control-api.onrender.com/api/health     # {"ok":true,...}
curl -s https://bnb-mission-control-api.onrender.com/api/pillars  # nodereal.reachable / chain_id / sponsorable
# CORS preflight from the Vercel origin:
curl -si -X OPTIONS https://bnb-mission-control-api.onrender.com/api/snapshot \
     -H 'Origin: https://bnb-mission-control-two.vercel.app' \
     -H 'Access-Control-Request-Method: GET' | grep -i access-control-allow-origin
```

---

## Part B — Point the Vercel SPA at the Render API

The SPA resolves its API base from [`web/public/config.json`](../web/public/config.json)
(runtime) or the `VITE_API_BASE` build env (takes precedence). `config.json` is
already set to `https://bnb-mission-control-api.onrender.com`.

- **If the Render service URL matches** that value → just **redeploy on Vercel**
  (push, or Vercel → Deployments → Redeploy) so it picks up the committed
  `config.json`. The dashboard now polls live data + shows `live api` in the
  freshness chip.
- **If the URL differs** (name taken → random suffix) → either edit
  `web/public/config.json` to the real URL and redeploy, or set
  `VITE_API_BASE=https://<your-api>.onrender.com` in Vercel → Settings →
  Environment Variables and redeploy.

> CORS: the API allows the Vercel origin via `API_CORS_ORIGINS` in `render.yaml`.
> If you use a different Vercel domain, update that value (comma-separated list,
> or `*` for any origin) and redeploy Render.

---

## Security & 2FA

These keys touch **real mainnet funds**, so the deploy is designed to keep every
fund-controlling secret OFF the cloud.

**1. Never deploy a signing key to the dashboard.** It's read-only — it shows the
identity wallet via the **public** `AGENT_IDENTITY_ADDRESS` and reads balances by
address. The following stay **only on your local machine** (where the agent signs):
`AGENT_PRIVATE_KEY`, `AGENT_WALLET_PASSWORD`, `TWAK_ACCESS_ID`, `TWAK_HMAC_SECRET`,
`TWAK_WALLET_PASSWORD`. The blueprint does not list them; don't add them.

**2. Keep funds minimal + segregated.**
- The **TWAK trading wallet** (`0xE8A3…6215`) holds the trading BNB/USDT; its key is
  TWAK-custodied and never enters env/cloud. Live trading runs locally only.
- The **identity/x402 wallet** (`0xEb7bF…9655`): keep only ~$1–2 USDC on Base (enough
  for x402 micropayments) so even a worst-case key compromise loses almost nothing.

**3. Scope the NodeReal / MegaFuel key (if you deploy it).** In the NodeReal
dashboard, set the MegaFuel **sponsor policy** to whitelist *only* the ERC-8004
registry + your wallet, with a **daily/total gas-spend cap**. Then a leaked
`NODEREAL_API_KEY` can at most burn that capped gas budget — it can't touch funds.
Or omit the key entirely (see secrets table).

**4. Turn on 2FA everywhere these keys live or deploy from:**

| Account | 2FA | Why / mitigation |
|---|---|---|
| **GitHub** (`kaustubh76/BNB`) | enable | repo is private; 2FA stops a takeover from exposing history/secrets |
| **Render** | ❌ not on free plan | **can't enable 2FA on free** → mitigated by deploying **zero secrets** (nothing to leak) |
| **Vercel** | enable | controls the deployed UI + its env (no secrets there either) |
| **NodeReal** | enable | controls the MegaFuel gas tank + sponsor policy (key kept local) |
| **CoinMarketCap** | enable | controls the API key/quota (key kept local) |

> Because Render free has no 2FA, the deploy is built so a Render compromise leaks
> **nothing** — no key is stored there. That's the whole point of the zero-secret design.

**5. Rotate anything that was ever exposed.** `.env` is git-ignored now, but if it
was committed earlier in history (private repo, so not public — still visible to any
collaborator), rotate the keys that were in it: `CMC_API_KEY`, `NODEREAL_API_KEY`,
`TWAK_*`, and **re-create the identity wallet** if `AGENT_PRIVATE_KEY` was ever
committed. Scrubbing history (`git filter-repo`) is optional once rotated.

**6. Tighten CORS** once stable: `API_CORS_ORIGINS` is pinned to your Vercel origin
(not `*`), so only your SPA can call the API from a browser.

## Dynamism & persistence

- **Live by design:** pillar status (NodeReal link, x402 wallet/balance/receipts)
  is computed server-side per request with a 60 s TTL cache — dynamic on every plan.
- **Free tier** has no persistent disk and sleeps after 15 min idle, so the journal
  is **reseeded** (one sim tick) on each cold start. Keep it warm with an
  [UptimeRobot](https://uptimerobot.com) HTTP monitor on `/api/health` every 5 min.
- **Continuously-updating NAV feed (paid):** upgrade the instance, add a disk so the
  journal persists, and run the allocator loop alongside the API. In `render.yaml`,
  under the service:

  ```yaml
      disk:
        name: bnb-data
        mountPath: /app/data
        sizeGB: 1
  ```

  then schedule `python scripts/run_allocator.py --loop --interval-min 240`. The API
  reads the growing `data/journal/allocator_journal.jsonl` and the NAV curve advances.

## Static fallback

The SPA also ships a committed `web/public/snapshot.json` (offline fallback for when
the API is cold). Refresh it before a Vercel deploy with `make snapshot`.

---

## Operations

| Task | How |
|---|---|
| Tail API logs | Render → service → **Logs** |
| Redeploy API | push to the connected branch (auto-deploy on) |
| Redeploy SPA | Vercel → Deployments → Redeploy (or push) |
| Update secrets | Render → service → **Environment** → edit → Save |
| Show the live track | set `DASHBOARD_JOURNAL=live` (+ `TWAK_MODE=live`) once the contest track runs |
| Run the old Streamlit UI locally | `pip install -e ".[ui]"` then `streamlit run src/ictbot/ui/app.py` |
