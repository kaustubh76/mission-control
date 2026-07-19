import { useMemo, useState } from "react";

import {
  getHealth,
  getMarketDataHub,
  getMarketIntel,
  getNav,
  getRationale,
  getState,
  getStrategy,
  pingCmcApi,
  rereadWallet,
  verifyPillars,
} from "../api/client";
import type { Regime } from "../api/types";
import type { UseAllocator } from "../hooks/useAllocator";
import { fmtUsd } from "../lib/format";
import type { GlossaryKey } from "../lib/glossary";
import Card from "./ui/Card";
import StatusPill, { type Tone } from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

type Status = "ok" | "warn" | "fail";
interface Result {
  status: Status;
  detail: string;
}
interface Check {
  key: string;
  label: string;
  term?: GlossaryKey;
  run: () => Promise<Result>;
}

const TONE: Record<Status, Tone> = { ok: "up", warn: "warn", fail: "down" };
const DOT: Record<Status, string> = { ok: "#16c784", warn: "#f0b90b", fail: "#ea3943" };
const ageLabel = (s: number | null | undefined) =>
  s == null ? "—" : s < 90 ? `${Math.round(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;

// Every probe is a read-only GET — none trade or mutate state.
// `rebalanceCount` is threaded in from the already-loaded dashboard snapshot so the
// "Rebalance journal" row stays consistent with the Rebalances table (rather than an
// independent /api/rebalances read that hits the thinner live journal).
function buildChecks(rebalanceCount: number | null, regime: Regime | null): Check[] {
  return [
  {
    key: "health",
    label: "API health",
    run: async () => {
      const h = await getHealth();
      if (!h.ok) return { status: "fail", detail: "health.ok = false" };
      if (h.kill_switch_engaged) return { status: "warn", detail: "kill switch ENGAGED" };
      if (h.journal_mismatch) return { status: "warn", detail: "journal/mode mismatch" };
      const hb = h.heartbeat_age_s == null ? "no heartbeat (sim)" : `heartbeat ${ageLabel(h.heartbeat_age_s)} ago`;
      return { status: "ok", detail: `mode ${h.mode} · ${hb}` };
    },
  },
  {
    key: "strategy",
    label: "Strategy config",
    run: async () => {
      const s = await getStrategy();
      const n = s.active?.length ? s.active.length : s.tokens.length;
      if (!(s.params.top_k > 0 && s.tokens.length > 0)) return { status: "fail", detail: "params not loaded" };
      return { status: "ok", detail: `top-${Math.min(s.params.top_k, n)} of ${n}` };
    },
  },
  {
    key: "state",
    label: "Allocator state",
    run: async () => {
      const st = await getState();
      if (!(st.nav != null && st.nav > 0)) return { status: "fail", detail: "no valid NAV" };
      if (st.halted) return { status: "warn", detail: `HALTED — ${st.halt_reason ?? "drawdown"}` };
      return { status: "ok", detail: `NAV ${fmtUsd(st.nav)} · ${st.cumulative_swaps} swaps` };
    },
  },
  {
    key: "risk",
    label: "Risk / drawdown",
    term: "drawdown",
    run: async () => {
      const nv = await getNav();
      const dd = nv.drawdown.current;
      const pct = `${(dd * 100).toFixed(1)}%`;
      if (dd >= nv.caps.dq) return { status: "fail", detail: `${pct} ≥ DQ ${(nv.caps.dq * 100).toFixed(0)}%` };
      if (dd >= nv.caps.team) return { status: "warn", detail: `${pct} ≥ team cap ${(nv.caps.team * 100).toFixed(0)}%` };
      return { status: "ok", detail: `drawdown ${pct} · safe` };
    },
  },
  {
    key: "regime",
    label: "Regime / Fear&Greed",
    term: "fearGreed",
    // Read the already-loaded snapshot's regime (consistent with the hero card) instead of an
    // independent /api/regime probe that re-reads the thinner/aging seed journal and falsely
    // reports "(cached)". Mirrors the `rebalances` check below. Still honest: a genuinely stale
    // served snapshot carries `stale=true`, so "(cached)" only shows when the data really is.
    run: async () => {
      if (!regime || regime.fear_greed == null) return { status: "warn", detail: "F&G unavailable" };
      if (regime.stale) return { status: "warn", detail: `F&G ${regime.fear_greed} (cached)` };
      return { status: "ok", detail: `F&G ${regime.fear_greed} · ${regime.fear_greed_label}` };
    },
  },
  {
    key: "cmc",
    label: "Data API budget",
    term: "credits",
    run: async () => {
      const c = await pingCmcApi();
      // A read-only data key absent on this deploy is a config state, not a system failure.
      if (!c.key_set) return { status: "warn", detail: "read-only data key not set on this deploy" };
      if (c.near_cap_day || c.near_cap_month) return { status: "warn", detail: "near credit cap" };
      if (!c.healthy) return { status: "warn", detail: `last status ${c.last_status ?? "—"}` };
      return { status: "ok", detail: `${c.credits_today}/${c.daily_budget} credits today` };
    },
  },
  {
    key: "wallet",
    label: "On-chain wallet",
    term: "realFunds",
    run: async () => {
      const w = await rereadWallet();
      if (!w.ok || w.total_usd == null) return { status: "fail", detail: w.note ?? "wallet read failed" };
      if (w.gas_low) return { status: "warn", detail: `${fmtUsd(w.total_usd)} · gas low` };
      return { status: "ok", detail: `${fmtUsd(w.total_usd)} · block ${w.block ?? "—"}` };
    },
  },
  {
    key: "intel",
    label: "Market intelligence",
    run: async () => {
      const m = await getMarketIntel();
      if (!m.enabled) return { status: "warn", detail: "market intel off" };
      const has = !!m.global_metrics || !!m.regime_terms;
      return has ? { status: "ok", detail: "global metrics live" } : { status: "warn", detail: "enabled, no data yet" };
    },
  },
  {
    key: "pillars",
    label: "On-chain identity (NodeReal)",
    term: "erc8004",
    run: async () => {
      const p = await verifyPillars();
      const n = p.nodereal;
      if (!n.api_key_set) return { status: "warn", detail: "key-free deploy" };
      // A transient/unreachable RPC on the read-only deploy is degraded, not a hard system failure.
      if (!(n.reachable && n.chain_ok)) return { status: "warn", detail: n.note ?? "RPC unreachable — degraded" };
      if (!n.sponsorable) return { status: "warn", detail: `chain ${n.chain_id} · policy off` };
      return { status: "ok", detail: `chain ${n.chain_id} · agent #${n.agent_id || "unminted"}` };
    },
  },
  {
    key: "rebalances",
    label: "Rebalance journal",
    term: "rebalance",
    // Count the rebalances the dashboard already shows (loaded snapshot) — consistent with
    // the Rebalances table; avoids an independent /api/rebalances read of the thinner journal.
    run: async () => {
      if (rebalanceCount == null) return { status: "warn", detail: "snapshot not loaded" };
      return rebalanceCount > 0
        ? { status: "ok", detail: `${rebalanceCount} rebalances` }
        : { status: "warn", detail: "no rebalances yet" };
    },
  },
  {
    key: "rationale",
    label: "Rationale feed",
    run: async () => {
      const rt = await getRationale();
      return rt.items.length > 0
        ? { status: "ok", detail: `${rt.items.length} entries` }
        : { status: "warn", detail: "no rationale yet" };
    },
  },
  {
    key: "hub",
    label: "Market Data Hub (free)",
    run: async () => {
      const hub = await getMarketDataHub();
      if (!hub || !hub.enabled) return { status: "warn", detail: "free data off" };
      const tokens = Object.keys(hub.dex ?? {}).length;
      const regime = hub.overview?.regime ?? "—";
      return { status: "ok", detail: `${regime} · ${tokens} tokens · DexScreener` };
    },
  },
  ];
}

export default function SystemDiagnostics({ allocator }: { allocator: UseAllocator }) {
  const { live, data } = allocator;
  const rebalanceCount = data?.rebalances?.items?.length ?? null;
  const regime = data?.regime ?? null;
  const CHECKS = useMemo(() => buildChecks(rebalanceCount, regime), [rebalanceCount, regime]);
  const [results, setResults] = useState<Record<string, Result>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [running, setRunning] = useState(false);

  async function runOne(c: Check) {
    setBusy((b) => ({ ...b, [c.key]: true }));
    let r: Result;
    try {
      r = await c.run();
    } catch (e) {
      r = { status: "fail", detail: e instanceof Error ? e.message : "probe failed" };
    }
    setResults((prev) => ({ ...prev, [c.key]: r }));
    setBusy((b) => ({ ...b, [c.key]: false }));
    return r;
  }

  async function runAll() {
    setRunning(true);
    await Promise.allSettled(CHECKS.map(runOne));
    setRunning(false);
  }

  const done = CHECKS.filter((c) => results[c.key]);
  const passed = done.filter((c) => results[c.key].status === "ok").length;
  const failed = done.filter((c) => results[c.key].status === "fail").length;

  return (
    <Card
      label={
        <span className="inline-flex items-center gap-1">
          System Diagnostics <InfoTip term="diagnostics" side="bottom" />
        </span>
      }
      accent="#3861fb"
      right={
        <span className="flex items-center gap-2">
          {done.length > 0 && (
            <StatusPill
              tone={failed > 0 ? "down" : passed === done.length ? "up" : "warn"}
              srText={`${passed} of ${done.length} checks passing`}
            >
              {passed}/{done.length} OK{failed > 0 ? ` · ${failed}✗` : ""}
            </StatusPill>
          )}
          <button
            onClick={runAll}
            disabled={running}
            aria-busy={running}
            className="rounded-sm border-3 border-cool/50 bg-cool/10 px-3 py-1 font-display text-xs font-bold text-cyan shadow-brut-sm transition hover:bg-cool/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {running ? "running…" : "▶ Run all checks"}
          </button>
        </span>
      }
    >
      <div className="mb-3 text-[10px] leading-snug text-muted/70">
        Read-only probes — API, strategy, wallet, risk &amp; on-chain identity
      </div>
      {!live && (
        <div className="mb-3 rounded-sm border border-amber/40 bg-amber/10 px-2 py-1 text-[11px] text-amber">
          demo snapshot — probes hit the live API; connect the backend for real results
        </div>
      )}

      <ul className="grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-2">
        {CHECKS.map((c) => {
          const r = results[c.key];
          const isBusy = busy[c.key];
          return (
            <li key={c.key}>
              <button
                onClick={() => runOne(c)}
                disabled={isBusy}
                aria-label={`re-run ${c.label}`}
                className="flex min-h-[40px] w-full items-center gap-2 rounded-sm px-1.5 py-1.5 text-left transition hover:bg-panel2/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:opacity-60 sm:min-h-0"
              >
                <span
                  className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ background: r ? DOT[r.status] : "#3a3d46" }}
                />
                <span className="flex items-center gap-1 text-[12px] text-ink">
                  {c.label}
                  {c.term && <InfoTip term={c.term} />}
                </span>
                <span className="ml-auto truncate pl-2 text-right font-mono text-[10.5px] text-muted">
                  {isBusy ? "checking…" : r ? r.detail : "—"}
                </span>
                {r && !isBusy && (
                  <StatusPill tone={TONE[r.status]} srText={r.status}>
                    {r.status === "ok" ? "✓" : r.status === "warn" ? "!" : "✗"}
                  </StatusPill>
                )}
              </button>
            </li>
          );
        })}
      </ul>

      <p className="mt-3 border-t border-edge pt-2 text-[11px] leading-relaxed text-muted">
        Read-only probes — they confirm each subsystem is responding and never trade, rebalance, or
        change state. Click a row to re-run a single check.
      </p>
    </Card>
  );
}
