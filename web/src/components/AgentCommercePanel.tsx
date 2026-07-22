import { useState } from "react";
import type { GlossaryKey } from "../lib/glossary";
import type { CommerceBlock, CommerceCreateJobResult, CommercePreviewReport, CommerceSettleResult } from "../api/types";
import { postCommercePreview, postCreateCommerceJob, postSettleCommerce } from "../api/client";
import { shortHash, shortAddr, cmcLabel, clockHM, fmtPctRounded, regimeColor, fgColor, networkLabel, ipfsUrl } from "../lib/format";
import Card from "./ui/Card";
import Collapsible from "./ui/Collapsible";
import StatusPill from "./ui/StatusPill";
import VlayerBadge from "./VlayerBadge";
import InfoTip from "./ui/Tooltip";

// The SELL side of the agent economy (ERC-8183). Violet to distinguish from the blue Market-Hub
// (buy side). The two panels together tell the "two-sided agent economy" story for the SDK prize.
const COMMERCE_VIOLET = "#8b9dff";

function Tile({ label, value, color, tip }: { label: string; value: string; color?: string; tip?: GlossaryKey }) {
  return (
    <div className="rounded-sm border border-edge bg-panel2 px-2.5 py-1.5">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted">
        {label}
        {tip && <InfoTip term={tip} />}
      </div>
      <div
        className="font-mono text-sm font-semibold leading-tight break-words"
        style={{ color: color ?? "rgb(var(--c-ink))" }}
      >
        {value}
      </div>
    </div>
  );
}

function fmtU(n: number): string {
  return `${n.toLocaleString(undefined, { maximumFractionDigits: 6 })} U`;
}

// Exact U → wei (18dp) without float rounding: split on the decimal point and pad/truncate the
// fractional part to 18 digits, then BigInt-combine. "" / invalid → "0".
function uToWei(u: string): string {
  const m = (u ?? "").trim().match(/^(\d*)(?:\.(\d*))?$/);
  if (!m) return "0";
  const whole = m[1] || "0";
  const frac = (m[2] || "").slice(0, 18).padEnd(18, "0");
  return (BigInt(whole) * 10n ** 18n + BigInt(frac || "0")).toString();
}

/**
 * Agent Commerce — the agent SELLS its Market Regime Report to other agents over ERC-8183
 * (the agent-commerce SDK's flagship). The capability is REAL before the first on-chain job settles, so
 * the panel always shows the genuine offering (the advertised service + a live deliverable preview)
 * plus an on-chain job ledger that fills in honestly once a faucet-funded job settles. No fake jobs.
 */
export default function AgentCommercePanel({
  commerce,
  live = true,
}: {
  commerce?: CommerceBlock | null;
  live?: boolean;
}) {
  // "Create a job" — runs the REAL ERC-8183 loop (create→fund→serve→settle) on a LOCAL operator
  // run. `can_create` is false on the read-only cloud deploy (no signing key), so the button is
  // disabled there. Hooks must precede the early `if (!commerce)` return (rules of hooks).
  const canCreate = !!commerce?.can_create;
  const [jobQuery, setJobQuery] = useState("Give me your current market regime read + momentum ranking.");
  const [jobPayU, setJobPayU] = useState("0.1"); // U the buyer pays into escrow (real revenue)
  const [jobBusy, setJobBusy] = useState(false);
  const [jobResult, setJobResult] = useState<CommerceCreateJobResult | null>(null);
  const onCreateJob = async () => {
    if (!canCreate || jobBusy || !jobQuery.trim()) return;
    setJobBusy(true);
    setJobResult(null);
    try {
      const wei = uToWei(jobPayU);
      setJobResult(await postCreateCommerceJob(jobQuery, wei !== "0" ? wei : undefined));
    } catch (e) {
      setJobResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setJobBusy(false);
    }
  };

  // "Preview the report" — READ-ONLY: builds the live Market Regime Report the agent sells (no signing,
  // no spend), so it works EVERYWHERE, including the read-only cloud deploy where can_create is false.
  // A visitor clicks and sees the genuine deliverable, minus the on-chain escrow.
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewResult, setPreviewResult] = useState<CommercePreviewReport | null>(null);
  const onPreview = async () => {
    if (previewBusy || jobBusy || !jobQuery.trim()) return;
    setPreviewBusy(true);
    setPreviewResult(null);
    setJobResult(null);
    try {
      setPreviewResult(await postCommercePreview(jobQuery));
    } catch (e) {
      setPreviewResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setPreviewBusy(false);
    }
  };

  // Finalize served-but-unsettled jobs (operator-local). A job still in its ~7-day dispute window
  // comes back "deferred" — expected, not an error.
  const [settleBusy, setSettleBusy] = useState(false);
  const [settleResult, setSettleResult] = useState<CommerceSettleResult | null>(null);
  const onSettle = async () => {
    if (!canCreate || settleBusy) return;
    setSettleBusy(true);
    setSettleResult(null);
    try {
      setSettleResult(await postSettleCommerce());
    } catch (e) {
      setSettleResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setSettleBusy(false);
    }
  };

  const caption = (
    <span className="text-[10px] text-muted">
      buys data via x402 · sells analysis via ERC-8183
    </span>
  );

  // Truly absent (an ancient snapshot with no commerce block) → explain the capability, never blank.
  if (!commerce) {
    return (
      <Card
        label="Agent Commerce · ERC-8183"
        accent={COMMERCE_VIOLET}
        right={<StatusPill tone="neutral">snapshot</StatusPill>}
      >
        <div className="flex h-24 flex-col items-center justify-center gap-1 text-center text-xs text-muted">
          <div>The agent can sell its live Market Regime Report to other agents via ERC-8183 job escrow.</div>
          {caption}
        </div>
      </Card>
    );
  }

  const isTestnet = commerce.network === "bsc-testnet";
  const explorerBase = `https://${isTestnet ? "testnet." : ""}bscscan.com`;
  const served = commerce.jobs_served;
  const idle = served === 0;
  const armed = commerce.enabled;
  const service = commerce.service;
  const preview = commerce.preview;

  return (
    <Card
      label="Agent Commerce · ERC-8183"
      accent={COMMERCE_VIOLET}
      right={
        <span className="flex items-center gap-1.5">
          {caption}
          {/* The sold report's data provenance — always advertise it (honest pending/proven state). */}
          <VlayerBadge provenance={commerce.provenance} compact withTip={false} />
          <StatusPill tone={armed ? "up" : "neutral"} dot pulse={live && armed && !idle}>
            {armed ? `armed · ${networkLabel(commerce.network)}` : "config off"}
          </StatusPill>
        </span>
      }
    >
      <div className="space-y-3">
        {/* WHAT THE AGENT SELLS — the advertised service, anchored to its ERC-8004 identity. */}
        {service && (
          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-xs text-muted">sells</span>
              <span className="text-right font-mono text-sm font-semibold" style={{ color: COMMERCE_VIOLET }}>
                {cmcLabel(service.name)}
                <span className="ml-1 text-[10px] font-normal text-muted">{service.report_schema}</span>
              </span>
            </div>
            {service.capabilities.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {service.capabilities.map((c) => (
                  <span
                    key={c}
                    className="rounded-sm border border-edge bg-panel2 px-1.5 py-0.5 font-mono text-[10px] text-sub"
                  >
                    {cmcLabel(c)}
                  </span>
                ))}
              </div>
            )}
            {(service.agent_id > 0 || service.provider) && (
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted">
                {service.agent_id > 0 && (
                  <span>
                    identity <span className="font-mono text-sub">#{service.agent_id}</span>
                  </span>
                )}
                {service.provider && (
                  <a
                    href={`${explorerBase}/address/${service.provider}`}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-cyan hover:underline"
                  >
                    {shortAddr(service.provider)} ↗
                  </a>
                )}
              </div>
            )}
          </div>
        )}

        {/* LIVE DELIVERABLE PREVIEW — the genuine product the agent would hand over this instant. */}
        {preview ? (
          <div className="space-y-2 border-t border-edge pt-2">
            <div className="text-[10px] uppercase tracking-wider text-muted">live deliverable preview</div>
            <div className="grid grid-cols-3 gap-2">
              <Tile
                label="Regime"
                value={preview.regime_score != null ? preview.regime_score.toFixed(2) : "—"}
                color={regimeColor(preview.regime_score)}
              />
              <Tile label="Deploy cap" value={fmtPctRounded(preview.deploy_cap)} />
              <Tile
                label="Fear & Greed"
                value={preview.fear_greed != null ? String(preview.fear_greed) : "—"}
                color={fgColor(preview.fear_greed)}
              />
            </div>
            {preview.momentum_ranking.length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-[10px] uppercase tracking-wider text-muted">momentum</span>
                {preview.momentum_ranking.map((t, i) => (
                  <span
                    key={t}
                    className="rounded-sm border border-edge bg-panel2 px-1.5 py-0.5 font-mono text-[11px]"
                  >
                    {i + 1}. {cmcLabel(t)}
                  </span>
                ))}
              </div>
            )}
            {preview.rationale && (
              <div className="text-[11px] leading-snug text-sub">{cmcLabel(preview.rationale)}</div>
            )}
          </div>
        ) : (
          <div className="border-t border-edge pt-2 text-[11px] text-muted">
            Deliverable preview appears after the first allocator tick.
          </div>
        )}

        {/* ON-CHAIN JOB LEDGER — fills in for real once a faucet-funded ERC-8183 job settles. */}
        <div className="space-y-2 border-t border-edge pt-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-muted">on-chain jobs</span>
            <StatusPill tone={idle ? "neutral" : "up"}>
              {idle ? "awaiting first job" : `${served} served`}
            </StatusPill>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <Tile label="Jobs Served" value={String(served)} color={served > 0 ? "#16c784" : undefined} />
            <Tile label="Revenue" value={fmtU(commerce.revenue_u)} color={commerce.revenue_u > 0 ? "#16c784" : undefined} />
            <Tile label="Network" value={networkLabel(commerce.network)} />
          </div>
          <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
            <span className="rounded-sm border border-edge bg-panel2 px-1.5 py-0.5 font-mono">
              created {commerce.jobs_created}
            </span>
            <span className="rounded-sm border border-edge bg-panel2 px-1.5 py-0.5 font-mono">
              funded {commerce.jobs_funded}
            </span>
            <span className="rounded-sm border border-edge bg-panel2 px-1.5 py-0.5 font-mono">
              settled {commerce.jobs_settled}
            </span>
          </div>
        </div>

        {/* Job detail + the interactive create/serve form — collapsed by default to keep the panel tidy. */}
        <Collapsible title="ask the agent · job detail" id="commerce-detail" defaultOpen={false}>
          <div className="space-y-2">
          {(commerce.last_deliverable_hash || commerce.last_tx) && (
            <div className="space-y-1 font-mono text-[11px]">
              {commerce.last_deliverable_hash && (
                <div className="flex justify-between gap-2">
                  <span className="text-muted">last deliverable</span>
                  {commerce.last_deliverable_url ? (
                    // The deliverable is pinned on IPFS — link straight to the real product the agent sold.
                    <a
                      href={ipfsUrl(commerce.last_deliverable_url)}
                      target="_blank"
                      rel="noreferrer"
                      className="text-cyan hover:underline"
                    >
                      {shortHash(commerce.last_deliverable_hash)} ↗
                    </a>
                  ) : (
                    <span className="text-sub">{shortHash(commerce.last_deliverable_hash)}</span>
                  )}
                </div>
              )}
              {commerce.last_tx && (
                <div className="flex justify-between gap-2">
                  <span className="text-muted">submit tx</span>
                  <a
                    href={`${explorerBase}/tx/${commerce.last_tx}`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-cyan hover:underline"
                  >
                    {shortHash(commerce.last_tx)} ↗
                  </a>
                </div>
              )}
            </div>
          )}

          {/* Settlement is OPTIMISTIC — a served job sits "awaiting finalization" until the kernel's
              ~7-day dispute window elapses, then settle() finalizes it. The honest count (instead of a
              bare settled=0 that reads as failure) + an operator-local Finalize button. */}
          {commerce.jobs_pending_settle > 0 && (
            <div className="space-y-1.5">
              <div className="text-[10px] leading-snug text-muted">
                {commerce.jobs_settled}/{served} settled · {commerce.jobs_pending_settle} awaiting finalization
                {canCreate
                  ? " — finalize once each job's ~7-day dispute window closes."
                  : " — settlement is optimistic; finalizes after the ~7-day dispute window."}
              </div>
              {canCreate && (
                <button
                  onClick={onSettle}
                  disabled={settleBusy}
                  title="Finalize served jobs whose optimistic dispute window has closed (settle on-chain)"
                  className="w-full rounded-sm border-3 border-cool/50 bg-cool/10 px-3 py-1.5 font-display text-xs font-bold text-cyan shadow-brut-sm transition hover:bg-cool/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {settleBusy ? "finalizing…" : `Finalize settlements (${commerce.jobs_pending_settle})`}
                </button>
              )}
              {settleResult && (
                <div className="rounded-sm border border-edge bg-panel2 px-2 py-1 text-[10px]">
                  {(settleResult.settled?.length ?? 0) > 0 ? (
                    <span style={{ color: "#16c784" }}>
                      ✓ {settleResult.settled!.length} settled
                      {(settleResult.deferred?.length ?? 0) > 0
                        ? ` · ${settleResult.deferred!.length} still deferred (window open)`
                        : ""}
                    </span>
                  ) : (settleResult.deferred?.length ?? 0) > 0 ? (
                    <span style={{ color: "#f0b90b" }}>
                      {settleResult.deferred!.length} still deferred — dispute window not closed yet; retry later.
                    </span>
                  ) : (
                    <span className="text-muted">{settleResult.message || "nothing to finalize"}</span>
                  )}
                </div>
              )}
            </div>
          )}

        {/* CREATE A JOB — buy side. Runs the genuine ERC-8183 loop (create→fund→serve→settle) on a
            LOCAL operator run so the ledger above fills with a REAL served job. Disabled on the
            read-only cloud deploy (no signing key) — never a fake job. */}
        <div className="space-y-2 border-t border-edge pt-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wider text-muted">ask the agent</span>
            {!canCreate && <span className="text-[10px] text-muted">preview here · signing is operator-only</span>}
          </div>
          <textarea
            value={jobQuery}
            onChange={(e) => setJobQuery(e.target.value)}
            rows={2}
            disabled={jobBusy || previewBusy}
            placeholder="Ask the agent for a market regime report…"
            className="w-full rounded-sm border border-edge bg-panel2 px-2 py-1.5 text-[11px] text-sub outline-none disabled:opacity-50"
          />
          {/* pay — only meaningful for the REAL on-chain job (operator-local) */}
          {canCreate && (
            <label className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted">
              pay
              <input
                type="number"
                min="0"
                step="0.01"
                value={jobPayU}
                onChange={(e) => setJobPayU(e.target.value)}
                disabled={jobBusy}
                className="w-20 rounded-sm border border-edge bg-panel2 px-2 py-1 text-right font-mono text-[11px] text-sub outline-none disabled:opacity-50"
              />
              <span className="font-mono normal-case">U</span>
            </label>
          )}
          {/* Preview — READ-ONLY (no signing, no spend): the genuine deliverable, works EVERYWHERE
              incl. the read-only cloud deploy. This is what makes the button clickable for visitors. */}
          <button
            onClick={onPreview}
            disabled={previewBusy || jobBusy || !jobQuery.trim()}
            title="Read-only: build the live Market Regime Report this agent sells (no signing, no spend)"
            className="w-full rounded-sm border-3 border-cool/50 bg-cool/10 px-3 py-1.5 font-display text-xs font-bold text-cyan shadow-brut-sm transition hover:bg-cool/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {previewBusy ? "building report…" : "Preview the report"}
          </button>
          {/* Create + serve — the REAL on-chain loop; operator-local only (needs BOTH signing keys),
              so it appears only where can_create is true. Never shown (and never fakes) on the cloud. */}
          {canCreate && (
            <button
              onClick={onCreateJob}
              disabled={jobBusy || !jobQuery.trim()}
              title="Run the real ERC-8183 loop: create → fund → serve → settle"
              className="w-full rounded-sm border-3 border-violet/50 bg-violet/10 px-3 py-1.5 font-display text-xs font-bold text-violet shadow-brut-sm transition hover:bg-violet/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {jobBusy ? "creating job on-chain…" : "Create + serve a real job"}
            </button>
          )}
          {jobResult && (
            <div className="rounded-sm border border-edge bg-panel2 px-2 py-1.5 text-[11px]">
              {jobResult.ok ? (
                <div className="space-y-1">
                  <div style={{ color: "#16c784" }}>
                    ✓ served job #{jobResult.job_id}
                    {jobResult.status && jobResult.status !== "settle-deferred" ? ` (${jobResult.status})` : ""}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono">
                    {jobResult.tx && (
                      <a
                        href={`${explorerBase}/tx/${jobResult.tx}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-cyan hover:underline"
                      >
                        submit tx {shortHash(jobResult.tx)} ↗
                      </a>
                    )}
                    {jobResult.deliverable_url && (
                      // The deliverable the agent just sold, pinned on IPFS — clickable proof.
                      <a
                        href={ipfsUrl(jobResult.deliverable_url)}
                        target="_blank"
                        rel="noreferrer"
                        className="text-cyan hover:underline"
                      >
                        deliverable ↗
                      </a>
                    )}
                  </div>
                  {jobResult.status === "settle-deferred" && (
                    <div className="text-[10px] leading-snug text-muted">
                      optimistic settlement — finalizes automatically after the ~7-day dispute window; nothing to do.
                    </div>
                  )}
                </div>
              ) : jobResult.need != null ? (
                <div style={{ color: "#f0b90b" }}>
                  fund buyer {jobResult.buyer ? shortAddr(jobResult.buyer) : ""} with ≥ {jobResult.need}{" "}
                  {jobResult.token ?? "U"}
                  {jobResult.network ? ` on ${networkLabel(jobResult.network)}` : ""} (have {jobResult.have ?? 0}), then retry
                </div>
              ) : (
                <div className="text-muted">{jobResult.message || "job failed"}</div>
              )}
            </div>
          )}
          {/* READ-ONLY preview result — the genuine deliverable a buyer receives, minus on-chain escrow. */}
          {previewResult && (
            <div className="rounded-sm border border-edge bg-panel2 px-2 py-1.5 text-[11px]">
              {previewResult.ok && previewResult.status === "ok" ? (
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-ink">
                      live market regime report{previewResult.strategy ? ` · ${cmcLabel(previewResult.strategy)}` : ""}
                    </span>
                    <StatusPill tone="up" dot pulse srText="freshly computed live">
                      LIVE
                    </StatusPill>
                    {previewResult.ts && (
                      <span className="ml-auto text-[9px] text-muted/70">
                        computed live · as of {clockHM(previewResult.ts)}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-sub">
                    {previewResult.regime_score != null && (
                      <span>
                        regime{" "}
                        <span style={{ color: regimeColor(previewResult.regime_score) }}>
                          {previewResult.regime_score.toFixed(2)}
                        </span>
                      </span>
                    )}
                    {previewResult.deploy_cap != null && <span>deploy cap {fmtPctRounded(previewResult.deploy_cap)}</span>}
                    {previewResult.ta_health != null && <span>MCP TA {fmtPctRounded(previewResult.ta_health)}</span>}
                    {previewResult.fear_greed != null && (
                      <span>
                        F&amp;G <span style={{ color: fgColor(previewResult.fear_greed) }}>{previewResult.fear_greed}</span>
                      </span>
                    )}
                  </div>
                  {/* Live composed-Skill signals — the more-dynamic market intel behind the regime read. */}
                  {previewResult.market_overview && (
                    <>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-sub">
                        {previewResult.market_overview.btc_dominance != null && (
                          <span>
                            BTC dom <span className="text-ink">{previewResult.market_overview.btc_dominance.toFixed(1)}%</span>
                          </span>
                        )}
                        {previewResult.market_overview.mktcap_change_24h != null && (
                          <span>
                            mktcap 24h{" "}
                            <span style={{ color: previewResult.market_overview.mktcap_change_24h >= 0 ? "#16c784" : "#ea3943" }}>
                              {previewResult.market_overview.mktcap_change_24h >= 0 ? "+" : ""}
                              {previewResult.market_overview.mktcap_change_24h.toFixed(2)}%
                            </span>
                          </span>
                        )}
                        {previewResult.market_overview.regime && (
                          <span>
                            regime <span className="uppercase text-ink">{previewResult.market_overview.regime}</span>
                          </span>
                        )}
                      </div>
                      {previewResult.market_overview.narratives && previewResult.market_overview.narratives.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1">
                          <span className="text-[9px] uppercase tracking-wider text-muted">trending</span>
                          {previewResult.market_overview.narratives.slice(0, 3).map((n, i) => (
                            <span
                              key={i}
                              className="rounded-sm border border-edge bg-panel2 px-1.5 py-0.5 font-mono text-[10px] text-sub"
                            >
                              {cmcLabel(n)}
                            </span>
                          ))}
                        </div>
                      )}
                      {previewResult.market_overview.headline && (
                        <div className="text-[10px] leading-snug text-muted">
                          {cmcLabel(previewResult.market_overview.headline)}
                        </div>
                      )}
                    </>
                  )}
                  {previewResult.dex_signals && Object.keys(previewResult.dex_signals).length > 0 && (
                    <div className="space-y-1">
                      <div className="text-[9px] uppercase tracking-wider text-muted">
                        DEX signals · DexScreener (free)
                      </div>
                      <div className="flex flex-wrap gap-1 font-mono text-[10px]">
                        {Object.entries(previewResult.dex_signals).slice(0, 8).map(([sym, d]) => (
                          <span key={sym} className="rounded-sm border border-edge bg-panel2 px-1.5 py-0.5 text-sub">
                            {cmcLabel(sym)}{" "}
                            {d.price_usd != null && (
                              <span className="text-ink">
                                ${d.price_usd.toLocaleString(undefined, { maximumSignificantDigits: 4 })}
                              </span>
                            )}
                            {d.price_change_h24 != null && (
                              <span style={{ color: d.price_change_h24 >= 0 ? "#16c784" : "#ea3943" }}>
                                {" "}
                                {d.price_change_h24 >= 0 ? "+" : ""}
                                {d.price_change_h24.toFixed(1)}%
                              </span>
                            )}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {previewResult.momentum_ranking && previewResult.momentum_ranking.length > 0 && (
                    <div className="text-[10px] text-muted">
                      ranking:{" "}
                      <span className="font-mono text-sub">
                        {previewResult.momentum_ranking.map(cmcLabel).join(" › ")}
                      </span>
                    </div>
                  )}
                  {previewResult.rationale && (
                    <div className="text-[10px] leading-snug text-muted">{cmcLabel(previewResult.rationale)}</div>
                  )}
                  {!canCreate && (
                    <div className="text-[10px] leading-snug text-muted">
                      preview only — run <span className="font-mono">make api_commerce</span> locally to serve a paid
                      on-chain job.
                    </div>
                  )}
                </div>
              ) : previewResult.ok ? (
                <div className="text-muted">
                  {previewResult.message || "report degraded — insufficient market history; try again shortly"}
                </div>
              ) : (
                <div className="text-muted">{previewResult.message || "preview failed"}</div>
              )}
            </div>
          )}
        </div>
          </div>
        </Collapsible>
      </div>
    </Card>
  );
}
