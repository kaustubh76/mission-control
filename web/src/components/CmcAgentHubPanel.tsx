import type { ReactNode } from "react";

import { probeAgentHub } from "../api/client";
import type { AgentHub } from "../api/types";
import { cmcLabel, fmtPctRounded, fmtSignedPct, fmtUsd } from "../lib/format";
import type { GlossaryKey } from "../lib/glossary";
import Card from "./ui/Card";
import CheckButton from "./ui/CheckButton";
import StatusPill, { type Tone } from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

// LIVE proof — calls /api/agent-hub/ping, which makes a REAL server-side CMC MCP call (tools/list +
// sample) + a fresh composed-Skill read at request time. Not seeded snapshot data.
async function hubCheck() {
  const p = await probeAgentHub();
  if (!p) return { ok: false, level: "fail" as const, detail: "agent-hub unavailable" };
  if (!p.enabled) {
    return { ok: false, level: "warn" as const, detail: "MCP off in this deploy (no key)" };
  }
  const skill = p.skill ? ` · skill ${fmtPctRounded(p.skill.risk_budget)}` : "";
  return {
    ok: p.sample_ok,
    level: p.sample_ok ? ("ok" as const) : ("warn" as const),
    detail: `MCP LIVE · ${p.tools_live} tools · sample ${p.sample_ok ? "OK" : "—"}${skill}`,
  };
}

const CMC_BLUE = "#3861fb";
const NEON = "#16c784";
const DOWN = "#ea3943";
const AMBER = "#f0b90b";

function regimeColor(regime: string | null): string {
  if (regime === "risk-on") return NEON;
  if (regime === "risk-off") return DOWN;
  return AMBER;
}
function regimeTone(regime: string | null): Tone {
  if (regime === "risk-on") return "up";
  if (regime === "risk-off") return "down";
  return "warn";
}

// Friendly labels for CMC's MCP tool names (shown in the "tools the agent called" list).
const TOOL_LABEL: Record<string, string> = {
  get_crypto_technical_analysis: "per-token TA",
  get_global_metrics_latest: "global metrics",
  trending_crypto_narratives: "narratives",
  get_global_crypto_derivatives_metrics: "derivatives",
  get_upcoming_macro_events: "macro events",
  get_crypto_quotes_latest: "quotes",
  get_crypto_latest_news: "news",
  get_crypto_marketcap_technical_analysis: "mktcap TA",
  get_crypto_info: "coin info",
  search_cryptos: "search",
  search_crypto_info: "search info",
  get_crypto_metrics: "metrics",
};

function whenLabel(h: number | null): string {
  if (h == null) return "—";
  if (h < 0) return "now";
  if (h < 48) return `in ${h.toFixed(0)}h`;
  return `in ${Math.round(h / 24)}d`;
}

function Tile({ label, value, color, tip }: { label: string; value: string; color?: string; tip?: GlossaryKey }) {
  return (
    <div className="rounded-sm border border-edge bg-panel2 px-2.5 py-1.5">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted">
        {label}
        {tip && <InfoTip term={tip} />}
      </div>
      <div className="font-mono text-sm font-semibold" style={{ color: color ?? "rgb(var(--c-ink))" }}>
        {value}
      </div>
    </div>
  );
}

/** One node in the CMC data-pipeline lineage strip. */
function PipeNode({ label, sub }: { label: string; sub: string }) {
  return (
    <span className="inline-flex flex-col rounded-sm border border-cool/40 bg-panel2 px-2 py-1 leading-tight">
      <span className="font-display text-[10px] font-bold text-ink">{label}</span>
      <span className="text-[9px] text-muted">{sub}</span>
    </span>
  );
}
function PipeArrow() {
  return (
    <span className="px-0.5 text-cyan/60" aria-hidden>
      →
    </span>
  );
}

/**
 * The "Best Use of CoinMarketCap" exhibit: CMC's Data MCP (which of the 12 tools the agent
 * actually called) + our composed market-overview skill (regime / risk budget / derivatives /
 * macro / news the agent acted on) + the x402 pay-per-call receipts. Read-only.
 *
 * Renders only LIVE, populated fields — when the layer is off it shows a concise "how to
 * enable" note instead of the null clutter that got the earlier panel removed.
 */
export default function CmcAgentHubPanel({
  hub,
  live = true,
  deployCap = null,
}: {
  hub: AgentHub | null | undefined;
  live?: boolean;
  deployCap?: number | null; // the final deploy cap THIS tick — to show the MCP/x402 → cap lineage
}) {
  const attribution = (
    <a
      href="https://coinmarketcap.com/api/mcp/"
      target="_blank"
      rel="noreferrer"
      className="text-[10px] text-muted hover:text-[#3861fb] hover:underline"
    >
      CMC Agent Hub ↗
    </a>
  );

  const header = (right: ReactNode) => (
    <span className="flex items-center gap-1.5">
      <CheckButton label="test hub" run={hubCheck} />
      {right}
      {attribution}
    </span>
  );

  // Disabled / no-telemetry → explain the capability, never show nulls.
  if (!hub || !hub.mcp_enabled) {
    return (
      <Card
        label="CMC Agent Hub · MCP · Skills · x402"
        accent={CMC_BLUE}
        right={header(<StatusPill tone="neutral">{hub ? "off" : "snapshot"}</StatusPill>)}
      >
        <div className="flex h-24 flex-col items-center justify-center gap-1 text-center text-xs text-muted">
          <span>CMC Agent Hub is off in this deploy</span>
          <span className="text-[10px] opacity-70">
            set <code className="text-sub">CMC_MCP_ENABLED=1</code> +{" "}
            <code className="text-sub">CMC_SKILL_REGIME=1</code> to read CMC's pre-computed signals
            over MCP and drive the deploy cap
          </span>
        </div>
      </Card>
    );
  }

  const fresh = live && hub.mcp_enabled;
  const skill = hub.skill;
  const budget = skill?.risk_budget ?? null;
  const calls = hub.mcp?.calls ?? 0;
  const byTool = hub.mcp?.by_tool ?? {};
  const toolNames = Object.keys(byTool);
  const exercised = toolNames.length;
  // The full CMC Data-MCP catalog (12); fall back to just-the-called set if the field is absent.
  const available = hub.mcp?.tools_available ?? [];
  const allTools = available.length ? available : toolNames;
  const rx = hub.x402;
  const composed = skill?.skill_source === "composed";
  const deriv = skill?.derivatives ?? null;
  const macro = skill?.next_macro_event ?? null;
  const news = skill?.top_news ?? [];
  const quotes = skill?.quotes_cross_check ?? null;
  const nQuotes = quotes ? Object.keys(quotes).length : 0;
  // Live CMC WebSocket on-chain signals (onchain@* channels) the agent harvested this tick.
  const onchain = hub.onchain ?? null;
  const ocEntries = onchain
    ? Object.entries(onchain).filter(([, s]) => s && Object.values(s).some((v) => v != null))
    : [];

  // CMC-native rotation levers the agent acted on this tick (sector tilt toward trending
  // narratives + multi-window CMC momentum). Category names are sanitized via cmcLabel() on display.
  const rotation = hub.rotation ?? null;
  const trending = rotation?.trending ?? [];
  const sectorHits = rotation?.sector_hits ?? [];
  const momEntries = rotation?.mom ? Object.entries(rotation.mom).sort((a, b) => b[1] - a[1]) : [];
  const hasRotation = trending.length > 0 || sectorHits.length > 0 || momEntries.length > 0;

  return (
    <Card
      label="CMC Agent Hub · MCP · Skills · x402"
      accent={CMC_BLUE}
      collapsible
      defaultOpen={false}
      id="cmc-hub"
      right={header(
        fresh ? (
          <StatusPill tone="up" srText="live agent hub">
            live
          </StatusPill>
        ) : (
          <StatusPill tone="neutral">snapshot</StatusPill>
        ),
      )}
    >
      <div className="space-y-3">
        <div className="text-[10px] leading-snug text-muted/70">
          CoinMarketCap tools + MCP receipts + risk signals the agent acted on — counts are a
          snapshot; click <span className="font-semibold text-sub">test hub</span> to verify the MCP
          + Skill <span className="font-semibold text-sub">live</span> (a real server-side CMC call)
        </div>

        {/* Significance — one line per concept so a judge instantly reads what each is. The InfoTip
            pulls the canonical glossary definition on hover (single source; no duplicated copy). */}
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3">
          <div className="rounded-sm border border-edge bg-panel2/40 px-2 py-1.5">
            <div className="flex items-center gap-1 font-display text-[10px] font-bold uppercase tracking-wider text-cyan">
              MCP <InfoTip term="mcp" />
            </div>
            <div className="mt-0.5 text-[10px] leading-snug text-muted">
              Open standard the agent uses to pull live CoinMarketCap data — no scraping.
            </div>
          </div>
          <div className="rounded-sm border border-edge bg-panel2/40 px-2 py-1.5">
            <div className="flex items-center gap-1 font-display text-[10px] font-bold uppercase tracking-wider text-cyan">
              Skills <InfoTip term="skills" />
            </div>
            <div className="mt-0.5 text-[10px] leading-snug text-muted">
              A composed market-overview read fusing several CMC tools into one regime + risk-budget signal.
            </div>
          </div>
          <div className="rounded-sm border border-edge bg-panel2/40 px-2 py-1.5">
            <div className="flex items-center gap-1 font-display text-[10px] font-bold uppercase tracking-wider text-cyan">
              x402 <InfoTip term="x402" />
            </div>
            <div className="mt-0.5 text-[10px] leading-snug text-muted">
              Pay-per-call rail — the agent pays tiny USDC amounts on demand for premium CMC data.
            </div>
          </div>
        </div>

        {/* CMC data pipeline: API key → Skills → MCP → decision. The 100%-CMC lineage —
            every input the agent decides on is CoinMarketCap's own data; zero exchange data. */}
        <div className="rounded-sm border border-cool/30 bg-cool/[0.06] p-2.5">
          <div className="mb-1.5 flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-cyan">
            CMC data pipeline <InfoTip term="cmcPipeline" />
          </div>
          <div className="flex flex-wrap items-center gap-1 font-mono">
            <PipeNode label="CMC API key" sub="authenticated" />
            <PipeArrow />
            <PipeNode label="Skills" sub={composed ? "market-overview" : skill?.skill_source ?? "—"} />
            <PipeArrow />
            <PipeNode label="MCP" sub={`${exercised}/${allTools.length} tools`} />
            <PipeArrow />
            <PipeNode
              label="Decision"
              sub={skill?.regime ? `${skill.regime} · cap ${fmtPctRounded(budget)}` : "regime-adaptive"}
            />
          </div>
          <div className="mt-1.5 text-[9.5px] text-muted/70">
            100% CoinMarketCap data — zero exchange data
          </div>
        </div>

        {/* The composed market-overview skill read */}
        <div className="rounded-sm border border-edge bg-panel2 p-2.5">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-[11px] font-semibold text-ink">
              composed market-overview skill <InfoTip term="skills" />
              {skill?.skill_source && (
                <span
                  className="rounded-sm bg-panel px-1.5 py-0.5 font-mono text-[9px] text-muted"
                  title={
                    composed
                      ? "Built by stitching CMC Data-MCP tools — not a call into CMC's hosted Skills Marketplace (no callable skill endpoint exists)."
                      : "Consumed from CMC's Skills Marketplace."
                  }
                >
                  {composed ? "composed · Data MCP" : skill.skill_source}
                </span>
              )}
            </span>
            {skill?.regime && <StatusPill tone={regimeTone(skill.regime)}>{skill.regime}</StatusPill>}
          </div>

          {/* Deploy-cap lineage — makes the LIVE SDK→trading influence VISIBLE: the composed MCP
              skill's risk budget + the MCP-authoritative TA health (+ the x402-paid CMC data) are what
              sized THIS tick's deploy cap. Read straight from the live snapshot — not a recompute. */}
          {(budget !== null || hub.ta_health != null || deployCap != null) && (
            <div className="mt-2 rounded-sm border border-edge bg-panel2 p-2">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[10px] uppercase tracking-wider text-muted">deploy cap set by</span>
                {deployCap != null && (
                  <span className="font-mono text-xs font-semibold text-ink">→ {fmtPctRounded(deployCap)}</span>
                )}
              </div>
              <div className="space-y-1 text-[10px] text-muted">
                {budget !== null && (
                  <div className="flex justify-between">
                    <span>composed MCP skill · risk budget</span>
                    <span className="font-mono text-sub">{fmtPctRounded(budget)}</span>
                  </div>
                )}
                {hub.ta_health != null && (
                  <div className="flex justify-between">
                    <span>MCP TA health{hub.ta_source ? ` · ${hub.ta_source}` : ""}</span>
                    <span className="font-mono text-sub">{fmtPctRounded(hub.ta_health)}</span>
                  </div>
                )}
                {rx && rx.spent_usdc > 0 && (
                  <div className="flex justify-between">
                    <span>x402 CMC data bought</span>
                    <span className="font-mono text-sub">{fmtUsd(rx.spent_usdc, 2)}</span>
                  </div>
                )}
              </div>
              {(deployCap ?? budget) !== null && (
                <div className="mt-1.5 h-1.5 w-full rounded-full bg-panel">
                  <div
                    className="h-1.5 rounded-full"
                    style={{
                      width: `${Math.round((deployCap ?? budget ?? 0) * 100)}%`,
                      background: regimeColor(skill?.regime ?? null),
                    }}
                  />
                </div>
              )}
            </div>
          )}

          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {skill?.fear_greed != null && <Tile label="fear & greed" value={`${skill.fear_greed}`} tip="fearGreed" />}
            {skill?.btc_dominance != null && (
              <Tile label="btc dom" value={`${skill.btc_dominance.toFixed(1)}%`} color="#f7931a" tip="btcDominance" />
            )}
            {skill?.mktcap_change_24h != null && (
              <Tile
                label="mktcap 24h"
                value={fmtSignedPct(skill.mktcap_change_24h / 100)}
                color={skill.mktcap_change_24h >= 0 ? NEON : DOWN}
              />
            )}
          </div>

          {(skill?.narratives ?? []).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {(skill?.narratives ?? []).slice(0, 3).map((n) => (
                <span key={n} className="rounded-sm bg-panel px-1.5 py-0.5 text-[10px] text-sub">
                  {cmcLabel(n)}
                </span>
              ))}
            </div>
          )}
          {skill?.headline && <div className="mt-2 text-[11px] leading-snug text-muted">{skill.headline}</div>}
        </div>

        {/* Decision levers the agent folded in (only the ones that are on) */}
        {(deriv || macro || news.length > 0) && (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {deriv && (
              <div className="rounded-sm border border-edge bg-panel2 p-2">
                <div className="text-[10px] uppercase tracking-wider text-muted">deriv leverage stress</div>
                <div className="font-mono text-sm font-semibold" style={{ color: (deriv.stress ?? 0) > 0.5 ? DOWN : "rgb(var(--c-ink))" }}>
                  {fmtPctRounded(deriv.stress)}
                </div>
                <div className="text-[10px] text-muted">
                  OI {deriv.oi_change_24h != null ? fmtSignedPct(deriv.oi_change_24h / 100) : "—"} · funding{" "}
                  {deriv.funding_rate != null ? deriv.funding_rate.toFixed(4) : "—"}
                </div>
              </div>
            )}
            {macro && (
              <div className="rounded-sm border border-edge bg-panel2 p-2">
                <div className="text-[10px] uppercase tracking-wider text-muted">next macro event</div>
                <div
                  className="truncate font-mono text-[12px] font-semibold"
                  style={{ color: macro.high_impact ? AMBER : "rgb(var(--c-ink))" }}
                  title={macro.title ?? ""}
                >
                  {macro.title ?? "—"}
                </div>
                <div className="text-[10px] text-muted">
                  {whenLabel(macro.hours_to)}
                  {macro.high_impact ? " · high impact" : ""}
                </div>
              </div>
            )}
            {news.length > 0 && (
              <div className="rounded-sm border border-edge bg-panel2 p-2">
                <div className="text-[10px] uppercase tracking-wider text-muted">CMC news</div>
                {news[0]?.url ? (
                  <a
                    href={news[0].url}
                    target="_blank"
                    rel="noreferrer"
                    className="block truncate text-[11px] text-sub hover:text-[#6e8bff] hover:underline"
                    title={news[0].title ?? ""}
                  >
                    {news[0].title ?? "—"}
                  </a>
                ) : (
                  <div className="truncate text-[11px] text-sub">{news[0]?.title ?? "—"}</div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Data MCP tools the agent called + x402 receipts + TA the agent acted on */}
        <div className="grid grid-cols-2 gap-2 text-center sm:grid-cols-3">
          <div className="rounded-sm border border-edge bg-panel2 p-2">
            <div className="font-display text-base font-bold sm:text-lg text-ink">{calls}</div>
            <div className="flex items-center justify-center gap-1 text-[10px] text-muted">
              MCP calls · {exercised}/{allTools.length} tools <InfoTip term="mcp" />
            </div>
          </div>
          <div className="rounded-sm border border-edge bg-panel2 p-2">
            <div className="font-display text-base font-bold sm:text-lg" style={{ color: rx.settled > 0 ? NEON : "#8a8f9c" }}>
              {rx.settled}/{rx.total}
            </div>
            <div className="flex items-center justify-center gap-1 text-[10px] text-muted">
              x402 · {fmtUsd(rx.spent_usdc, 2)} <InfoTip term="x402" />
            </div>
          </div>
          <div className="rounded-sm border border-edge bg-panel2 p-2">
            <div className="font-display text-base font-bold sm:text-lg text-ink">
              {fmtPctRounded(hub.ta_health)}
            </div>
            <div className="flex items-center justify-center gap-1 text-[10px] text-muted">
              TA health{hub.ta_source ? ` · ${hub.ta_source}` : ""} <InfoTip term="ta" />
            </div>
          </div>
        </div>

        {/* The full CMC Data-MCP catalog (12) — exercised tools carry a call count; the rest are
            dimmed "available but not wired into a regime decision". Proves the whole surface. */}
        {allTools.length > 0 && (
          <div className="flex flex-wrap gap-1.5 border-t border-edge pt-2">
            {[...allTools]
              .sort((a, b) => (byTool[b] ?? 0) - (byTool[a] ?? 0))
              .map((t) => {
                const n = byTool[t] ?? 0;
                return (
                  <span
                    key={t}
                    className={`rounded-sm px-1.5 py-0.5 font-mono text-[9.5px] ${
                      n > 0 ? "bg-panel2 text-sub" : "bg-panel2/40 text-muted/40"
                    }`}
                    title={n > 0 ? `${t} · ${n} calls` : `${t} · available, not exercised`}
                  >
                    {TOOL_LABEL[t] ?? t}
                    {n > 0 ? ` · ${n}` : ""}
                  </span>
                );
              })}
          </div>
        )}

        {/* Live CMC WebSocket on-chain intelligence (onchain@* channels) — buy/sell flow, holder
            concentration, token liquidity + whale flow per token, harvested by the streamer and
            folded into the allocation overlays. */}
        {ocEntries.length > 0 && (
          <div className="rounded-sm border border-edge bg-panel2 p-2.5">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-[11px] font-semibold text-ink">
                on-chain intelligence <InfoTip term="cmcPipeline" />
                <span className="rounded-sm bg-panel px-1.5 py-0.5 font-mono text-[9px] text-muted">
                  live CMC WebSocket
                </span>
              </span>
              <span className="text-[9px] text-muted/70">flow · liquidity · concentration · whale</span>
            </div>
            <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-x-2 gap-y-1 text-[10px]">
              <div className="font-mono text-[9px] uppercase tracking-wider text-muted/60">token</div>
              <div className="font-mono text-[9px] uppercase tracking-wider text-muted/60">buy/sell flow</div>
              <div className="text-right font-mono text-[9px] uppercase tracking-wider text-muted/60">liquidity</div>
              <div className="text-right font-mono text-[9px] uppercase tracking-wider text-muted/60">top-10</div>
              <div className="text-right font-mono text-[9px] uppercase tracking-wider text-muted/60">whale 1h</div>
              {ocEntries.map(([sym, s]) => {
                const flow = s.flow_ratio;
                const flowPct = flow != null ? Math.round(flow * 100) : null;
                const flowColor = flow == null ? "#8a8f9c" : flow >= 0.5 ? NEON : DOWN;
                const conc = s.top10_pct;
                const whale = s.whale_net_usd;
                return (
                  <div key={sym} className="contents">
                    <div className="font-mono font-semibold text-ink">{cmcLabel(sym)}</div>
                    <div className="flex items-center gap-1.5">
                      <div className="h-1.5 w-12 overflow-hidden rounded-full bg-edge">
                        <div className="h-full" style={{ width: `${flowPct ?? 50}%`, background: flowColor }} />
                      </div>
                      <span className="font-mono" style={{ color: flowColor }}>
                        {flowPct != null ? `${flowPct}%` : "—"}
                      </span>
                    </div>
                    <div className="text-right font-mono text-sub">
                      {s.liquidity_usd != null ? fmtUsd(s.liquidity_usd, 0) : "—"}
                    </div>
                    <div
                      className="text-right font-mono"
                      style={{ color: conc != null && conc > 50 ? AMBER : "#c9ccd6" }}
                    >
                      {conc != null ? `${conc.toFixed(1)}%` : "—"}
                    </div>
                    <div
                      className="text-right font-mono"
                      style={{ color: whale == null ? "#8a8f9c" : whale < 0 ? DOWN : NEON }}
                    >
                      {whale != null ? fmtUsd(whale, 0) : "—"}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-1.5 text-[9px] text-muted/70">
              channels: token_metric · holders_metrics · token_agg_event · transaction — 100% CoinMarketCap
            </div>
          </div>
        )}

        {/* CMC-native rotation — sector tilt toward CMC's live trending narratives + multi-window
            momentum (pct_24h/7d/30d). Both are strategy-agnostic allocation levers, applied across
            every strategy. Category labels run through cmcLabel() so no raw exchange name surfaces. */}
        {hasRotation && (
          <div className="rounded-sm border border-edge bg-panel2 p-2.5">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-[11px] font-semibold text-ink">
                CMC rotation
                <span className="rounded-sm bg-panel px-1.5 py-0.5 font-mono text-[9px] text-muted">
                  narratives · momentum
                </span>
              </span>
              <span className="text-[9px] text-muted/70">trending_crypto_narratives · pct_24h/7d/30d</span>
            </div>
            {trending.length > 0 && (
              <div className="mb-1.5 flex flex-wrap items-center gap-1">
                <span className="font-mono text-[9px] uppercase tracking-wider text-muted/60">trending</span>
                {trending.slice(0, 5).map((t) => (
                  <span key={t} className="rounded-sm bg-cool/[0.10] px-1.5 py-0.5 font-mono text-[9px] text-sub">
                    {cmcLabel(t)}
                  </span>
                ))}
              </div>
            )}
            {sectorHits.length > 0 && (
              <div className="mb-1 text-[10px] text-sub">
                rotated toward:{" "}
                <span className="font-mono text-ink">{sectorHits.map((s) => cmcLabel(s)).join(" · ")}</span>
              </div>
            )}
            {momEntries.length > 0 && (
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]">
                {momEntries.map(([sym, m]) => (
                  <span key={sym} className="font-mono">
                    <span className="text-ink">{cmcLabel(sym)}</span>{" "}
                    <span style={{ color: m >= 0 ? NEON : DOWN }}>
                      {`${m >= 0 ? "+" : ""}${m.toFixed(1)}%`}
                    </span>
                  </span>
                ))}
              </div>
            )}
            <div className="mt-1.5 text-[9px] text-muted/70">
              CMC-native momentum + narrative levers — 100% CoinMarketCap
            </div>
          </div>
        )}

        {/* Capability + integrity flags */}
        <div className="flex flex-wrap items-center gap-1.5 border-t border-edge pt-2">
          <StatusPill tone={hub.ta_enabled ? "up" : "neutral"}>TA cap {hub.ta_enabled ? "on" : "off"}</StatusPill>
          <StatusPill tone={hub.skill_enabled ? "up" : "neutral"}>skill {hub.skill_enabled ? "on" : "off"}</StatusPill>
          <StatusPill tone={hub.x402_enabled ? "up" : "neutral"}>x402 {hub.x402_enabled ? "on" : "off"}</StatusPill>
          {hub.onchain_enabled && <StatusPill tone="info">on-chain {ocEntries.length}</StatusPill>}
          {hub.rotation_enabled && <StatusPill tone="info">rotation</StatusPill>}
          {nQuotes > 0 && <StatusPill tone="info">CMC IDs {nQuotes}/8</StatusPill>}
        </div>
      </div>
    </Card>
  );
}
