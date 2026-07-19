import { useState } from "react";

import type { RebalanceItem, Rebalances } from "../api/types";
import { candleSourceLabel, clockHM, fmtUsd, shortHash, tokenColor } from "../lib/format";
import Card from "./ui/Card";
import InfoTip from "./ui/Tooltip";

function Weights({ w }: { w: Record<string, number> }) {
  const entries = Object.entries(w)
    .filter(([, v]) => v > 1e-4)
    .sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <span className="text-muted">all USDT</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {entries.map(([k, v]) => (
        <span
          key={k}
          className="rounded-sm px-1.5 py-0.5 text-[10px]"
          style={{ color: tokenColor(k), background: `${tokenColor(k)}1a` }}
        >
          {k} {(v * 100).toFixed(0)}%
        </span>
      ))}
    </span>
  );
}

function TxLinks({ tx }: { tx: { hash: string; url: string }[] }) {
  if (tx.length === 0) return <span className="text-muted">—</span>;
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
      {tx.map((t) => (
        <a key={t.hash} href={t.url} target="_blank" rel="noreferrer" className="font-mono text-cyan hover:underline">
          {shortHash(t.hash)} ↗
        </a>
      ))}
    </span>
  );
}

/** The drill-down behind a rebalance row: the full decision record for that tick. */
function RowDetail({ r }: { r: RebalanceItem }) {
  return (
    <div className="space-y-2.5 rounded-sm border border-edge bg-panel2/60 p-3 text-[11px]">
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <div>
          <div className="mb-1 text-[9px] uppercase tracking-wider text-muted">target weights</div>
          <Weights w={r.target} />
        </div>
        <div>
          <div className="mb-1 text-[9px] uppercase tracking-wider text-muted">achieved after swaps</div>
          <Weights w={r.weights_after} />
        </div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-edge/60 pt-2 font-mono text-[10px] text-sub">
        <span>NAV {r.nav_before != null ? fmtUsd(r.nav_before) : "—"} → {r.nav_after != null ? fmtUsd(r.nav_after) : "—"}</span>
        <span>fees {fmtUsd(r.fees_usd)}</span>
        <span>
          swaps {r.n_swaps}/{r.n_swaps_total}
          {r.n_failed > 0 && <span className="text-down"> · {r.n_failed} failed</span>}
        </span>
        <span className="uppercase">{r.mode}</span>
        {candleSourceLabel(r.candle_source) && (
          <span className="rounded-sm bg-cool/10 px-1.5 text-cyan" title="data provenance — the candle source the ranking ran on">
            {candleSourceLabel(r.candle_source)}
          </span>
        )}
      </div>
      {r.active_tokens && r.active_tokens.length > 0 && (
        <div>
          <div className="mb-1 text-[9px] uppercase tracking-wider text-muted">universe this tick</div>
          <span className="flex flex-wrap gap-1">
            {r.active_tokens.map((t) => (
              <span key={t} className="rounded-sm px-1.5 py-0.5 text-[10px]" style={{ color: tokenColor(t), background: `${tokenColor(t)}14` }}>
                {t}
              </span>
            ))}
          </span>
        </div>
      )}
      {r.n_failed > 0 && (
        <div>
          <div className="mb-1 text-[9px] uppercase tracking-wider text-down">failed swaps</div>
          <ul className="space-y-0.5 font-mono text-[10px] text-down/90">
            {r.failed_swaps.map((f, j) => (
              <li key={j}>
                {String(f.from ?? "?")}→{String(f.to ?? "?")} {f.amount != null ? `(${f.amount})` : ""}: {String(f.error ?? "failed")}
              </li>
            ))}
          </ul>
        </div>
      )}
      {r.tx.length > 0 && (
        <div>
          <div className="mb-1 text-[9px] uppercase tracking-wider text-muted">transactions</div>
          <TxLinks tx={r.tx} />
        </div>
      )}
      {r.rationale && <p className="border-t border-edge/60 pt-2 leading-relaxed text-sub">{r.rationale}</p>}
    </div>
  );
}

export default function RebalanceTable({ rebalances }: { rebalances: Rebalances }) {
  const items = rebalances.items;
  const [open, setOpen] = useState<string | null>(null);
  // Stable identity: ts + first tx hash (NOT the array index — the newest-first
  // list prepends on every tick, which would shift indexes and silently collapse
  // or mis-target an expanded row mid-read).
  const keyOf = (r: RebalanceItem) => `${r.ts}-${r.tx[0]?.hash ?? "none"}`;
  const toggle = (k: string) => setOpen((cur) => (cur === k ? null : k));

  return (
    <Card
      label="Recent Rebalances"
      accent="#3861fb"
      className="flex h-full flex-col"
      right={
        <span className="flex items-center gap-1.5 text-[11px] text-muted">
          <InfoTip term="rebalance" />
          {items[0]?.ts ? `as of ${clockHM(items[0].ts)} · ` : ""}{items.length} shown
        </span>
      }
    >
      {items.length === 0 ? (
        <div className="flex h-24 items-center justify-center text-xs text-muted">no rebalances journaled yet</div>
      ) : (
        <>
          {/* Desktop: dense table, rows expand to the full decision record */}
          <div className="-mx-1 hidden max-h-[300px] overflow-y-auto md:block">
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-muted">
                  <th className="w-4 px-1 pb-2" aria-label="expand" />
                  <th className="px-1 pb-2 font-medium">time</th>
                  <th className="px-1 pb-2 font-medium">held</th>
                  <th className="px-1 pb-2 text-right font-medium">swaps</th>
                  <th className="px-1 pb-2 text-right font-medium">fees</th>
                  <th className="px-1 pb-2 text-right font-medium">tx</th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => {
                  const k = keyOf(r);
                  const expanded = open === k;
                  return (
                    <RowPair key={k} r={r} expanded={expanded} onToggle={() => toggle(k)} />
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile: stacked cards, tap to expand the same detail */}
          <ul className="max-h-[340px] space-y-2 overflow-y-auto md:hidden">
            {items.map((r) => {
              const k = keyOf(r);
              const expanded = open === k;
              return (
                <li key={k} className="rounded-sm border border-edge bg-panel2/40 text-xs">
                  <button
                    onClick={() => toggle(k)}
                    aria-expanded={expanded}
                    className="block w-full p-2.5 text-left transition-colors duration-150 ease-out hover:bg-panel2/40 active:bg-panel2/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/50"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-sub">{clockHM(r.ts)}</span>
                      <span className="font-mono text-ink">
                        {r.n_swaps} swap{r.n_swaps === 1 ? "" : "s"}
                        {r.n_failed > 0 && <span className="ml-1 text-down">✗{r.n_failed}</span>}
                        <span className="text-muted"> · {fmtUsd(r.fees_usd)} fees</span>
                        <span className="ml-1.5 text-muted">{expanded ? "▾" : "▸"}</span>
                      </span>
                    </div>
                    <div className="mt-2">
                      <Weights w={r.weights_after} />
                    </div>
                  </button>
                  {expanded && (
                    <div className="px-2.5 pb-2.5">
                      <RowDetail r={r} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </Card>
  );
}

/** Desktop summary row + its expandable detail row. */
function RowPair({ r, expanded, onToggle }: { r: RebalanceItem; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
        tabIndex={0}
        aria-expanded={expanded}
        className={`cursor-pointer border-t border-edge/60 align-top transition-colors duration-150 ease-out hover:bg-panel2/50 focus:outline-none focus-visible:bg-panel2/70 ${expanded ? "bg-panel2/40" : ""}`}
      >
        <td className="px-1 py-2 text-[10px] text-muted">{expanded ? "▾" : "▸"}</td>
        <td className="whitespace-nowrap px-1 py-2 text-sub">{clockHM(r.ts)}</td>
        <td className="px-1 py-2">
          <Weights w={r.weights_after} />
          {r.x402_dex && (
            <div className="mt-1 text-[10px] text-cyan/70" title="x402 on-chain DEX read">
              x402: {r.x402_dex.symbol ?? r.x402_dex.q} {fmtUsd(r.x402_dex.price_usd, 2)}
            </div>
          )}
        </td>
        <td className="px-1 py-2 text-right font-mono text-ink">
          {r.n_swaps}
          {r.n_failed > 0 && <span className="ml-1 text-down">✗{r.n_failed}</span>}
        </td>
        <td className="px-1 py-2 text-right font-mono text-sub">{fmtUsd(r.fees_usd)}</td>
        <td className="px-1 py-2 text-right" onClick={(e) => e.stopPropagation()}>
          <span className="flex flex-col items-end gap-0.5">
            <TxLinks tx={r.tx} />
          </span>
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-edge/30">
          <td colSpan={6} className="px-1 py-2">
            <RowDetail r={r} />
          </td>
        </tr>
      )}
    </>
  );
}
