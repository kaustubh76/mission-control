// Typed fetch wrappers with a runtime-resolved API base, so the SAME static
// bundle works in every deployment shape:
//   - local / Render-served same-origin:  base = ""        → /api/...
//   - Vercel SPA → Render API:             VITE_API_BASE  (build) or /config.json
// When no live API answers (e.g. Render cold start), getSnapshot falls back to a
// committed /snapshot.json so the dashboard shows real (frozen) data, never blanks.
import type {
  AgentHub,
  MarketDataHub,
  AgentHubPing,
  CmcApi,
  CommerceCreateJobResult,
  CommercePreviewReport,
  CommerceSettleResult,
  Health,
  KillResult,
  MarketIntel,
  Nav,
  Pillars,
  Rebalances,
  Regime,
  RationaleFeed,
  Snapshot,
  State,
  Strategy,
  StrategySelectResult,
  TokensResult,
  Wallet,
} from "./types";

let _base: string | null = null;

async function resolveBase(): Promise<string> {
  if (_base !== null) return _base;
  const envBase = import.meta.env.VITE_API_BASE ?? "";
  if (envBase) {
    _base = envBase.replace(/\/$/, "");
    return _base;
  }
  // DEV: always same-origin so the Vite proxy routes /api to the LOCAL backend.
  // Without this, public/config.json (committed for the Vercel deploy) would
  // silently point a dev session at the production Render API.
  if (import.meta.env.DEV) {
    _base = "";
    return _base;
  }
  // Served from a local host (e.g. `make api_commerce` serving this built SPA at :8000): use
  // same-origin so the dashboard talks to the LOCAL keyed API — that's where signing keys live, so
  // it's the only place "Create Job" can work. (config.json points at the read-only Render API.)
  // `0.0.0.0` is included because uvicorn `--host 0.0.0.0` ADVERTISES that URL — the host the
  // operator actually clicks. Belt-and-suspenders: the local API also serves /config.json → "".
  if (
    typeof window !== "undefined" &&
    /^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])$/.test(window.location.hostname)
  ) {
    _base = "";
    return _base;
  }
  // Optional runtime override (lets a static Vercel deploy point at a live API
  // without a rebuild — just edit public/config.json and redeploy).
  try {
    const r = await fetch("/config.json", { cache: "no-store" });
    if (r.ok) {
      const c = (await r.json()) as { apiBase?: string };
      _base = (c.apiBase ?? "").replace(/\/$/, "");
      return _base;
    }
  } catch {
    /* no config.json — fine */
  }
  _base = "";
  return _base;
}

/** True when the last snapshot came from the committed static file (no live API). */
export let lastFromStatic = false;

export async function getSnapshot(): Promise<Snapshot> {
  const base = await resolveBase();
  try {
    // "no-cache" (not "no-store"): the browser still revalidates every poll, but participates in
    // ETag/304 + the shared edge cache so a repeat poll within the s-maxage window is served from
    // the edge (and a 304 skips re-download) instead of forcing a full origin rebuild. Data is never
    // stale — max-age=0 means the browser always revalidates before using a cached body.
    const r = await fetch(`${base}/api/snapshot`, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${r.status}`);
    const data = (await r.json()) as Snapshot;
    lastFromStatic = false;
    return data;
  } catch {
    const r = await fetch("/snapshot.json", { cache: "no-store" });
    if (!r.ok) throw new Error("no data source (live API and snapshot both failed)");
    lastFromStatic = true;
    return (await r.json()) as Snapshot;
  }
}

export async function postKill(engage: boolean, reason = "ui"): Promise<KillResult> {
  const base = await resolveBase();
  const r = await fetch(`${base}/api/controls/kill`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ engage, reason }),
  });
  return (await r.json()) as KillResult;
}

/** Set the active token universe. The server replies with a TokensResult body
 * even on 400 (current list + reason), so parse unconditionally — but a backend
 * WITHOUT this endpoint (old deploy) answers 404 `{"detail": …}`, which must
 * surface as an error, not be cast into a silent no-op. */
export async function postActiveTokens(active: string[]): Promise<TokensResult> {
  const base = await resolveBase();
  const r = await fetch(`${base}/api/controls/tokens`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ active }),
  });
  const body = (await r.json()) as Partial<TokensResult>;
  if (typeof body.ok !== "boolean") {
    throw new Error(`token controls unavailable on this API (HTTP ${r.status})`);
  }
  return body as TokensResult;
}

/** Set the SIM-track strategy. Server replies a StrategySelectResult even on 400
 * (unknown name); an old deploy without the endpoint answers 404, which must surface
 * as an error rather than a silent no-op. SIM-only — never touches the live strategy. */
export async function postStrategySelect(strategy: string): Promise<StrategySelectResult> {
  const base = await resolveBase();
  const r = await fetch(`${base}/api/controls/strategy`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ strategy }),
  });
  const body = (await r.json()) as Partial<StrategySelectResult>;
  if (typeof body.ok !== "boolean") {
    throw new Error(`strategy controls unavailable on this API (HTTP ${r.status})`);
  }
  return body as StrategySelectResult;
}

/** Create + serve a REAL ERC-8183 job (operator-local: create→fund→serve→settle). The server
 * always replies a CommerceCreateJobResult body — even on 403 (operator-only, no signing key) or
 * 409 (busy) — so parse unconditionally; an old deploy without the route answers 404, which must
 * surface as an error rather than a silent no-op. */
export async function postCreateCommerceJob(
  description: string,
  amountWei?: string,
): Promise<CommerceCreateJobResult> {
  const base = await resolveBase();
  // amount is in raw payment-token units (wei). Send as a string so large values keep full
  // precision through JSON (Pydantic coerces it to int); omit to use the server's service_price.
  const reqBody: { description: string; amount?: string } = { description };
  if (amountWei) reqBody.amount = amountWei;
  const r = await fetch(`${base}/api/commerce/create-job`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(reqBody),
  });
  const body = (await r.json()) as Partial<CommerceCreateJobResult>;
  if (typeof body.ok !== "boolean") {
    throw new Error(`commerce job control unavailable on this API (HTTP ${r.status})`);
  }
  return body as CommerceCreateJobResult;
}

/** FINALIZE served-but-unsettled ERC-8183 jobs (operator-local). Like postCreateCommerceJob, the
 * server always replies a CommerceSettleResult body — even on 403 (operator-only) or 409 (busy) —
 * so parse unconditionally; an old deploy without the route answers 404, surfaced as an error.
 * `jobIds` omitted → settle every job the ledger shows as awaiting finalization. */
export async function postSettleCommerce(jobIds?: number[]): Promise<CommerceSettleResult> {
  const base = await resolveBase();
  const reqBody: { job_ids?: number[] } = {};
  if (jobIds && jobIds.length) reqBody.job_ids = jobIds;
  const r = await fetch(`${base}/api/commerce/settle`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(reqBody),
  });
  const body = (await r.json()) as Partial<CommerceSettleResult>;
  if (typeof body.ok !== "boolean") {
    throw new Error(`commerce settle control unavailable on this API (HTTP ${r.status})`);
  }
  return body as CommerceSettleResult;
}

/** READ-ONLY preview of the agent's sellable Market Regime Report (no signing, no spend) — works
 * everywhere, including the zero-secret cloud deploy where create-job/settle 403. The server always
 * replies a CommercePreviewReport body; an old deploy without the route answers 404, surfaced as an
 * error rather than a silent no-op. */
export async function postCommercePreview(description: string): Promise<CommercePreviewReport> {
  const base = await resolveBase();
  const r = await fetch(`${base}/api/commerce/preview`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ description }),
  });
  const body = (await r.json()) as Partial<CommercePreviewReport>;
  if (typeof body.ok !== "boolean") {
    throw new Error(`commerce preview unavailable on this API (HTTP ${r.status})`);
  }
  return body as CommercePreviewReport;
}

/** Typed GET against the live API (no static fallback — self-tests must tell the
 * truth about whether the backend actually answered). */
async function getJson<T>(path: string): Promise<T> {
  const base = await resolveBase();
  const r = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

// Per-panel self-test reads + System Diagnostics probes (CheckButton / diagnostics
// targets). All read-only — none mutate state or trade.
export const pingCmcApi = () => getJson<CmcApi>("/api/cmc-api");
export const rereadWallet = () => getJson<Wallet>("/api/wallet");
export const verifyPillars = () => getJson<Pillars>("/api/pillars");
export const getHealth = () => getJson<Health>("/api/health");
export const getStrategy = () => getJson<Strategy>("/api/strategy");
export const getState = () => getJson<State>("/api/state");
export const getNav = () => getJson<Nav>("/api/nav");
export const getRegime = () => getJson<Regime>("/api/regime");
export const getMarketIntel = () => getJson<MarketIntel>("/api/market-intel");
export const getRebalances = () => getJson<Rebalances>("/api/rebalances");
export const getRationale = () => getJson<RationaleFeed>("/api/rationale");
export const getAgentHub = async (): Promise<AgentHub | null> =>
  (await getJson<Snapshot>("/api/snapshot")).agent_hub;
export const getMarketDataHub = async (): Promise<MarketDataHub | null | undefined> =>
  (await getJson<Snapshot>("/api/snapshot")).market_data_hub;
// LIVE on-demand probe — the server makes a real CMC MCP + Skill call at request time (not seeded).
export const probeAgentHub = () => getJson<AgentHubPing>("/api/agent-hub/ping");
