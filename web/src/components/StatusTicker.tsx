import type { Snapshot } from "../api/types";
import { fmtUsd, fmtSignedPct } from "../lib/format";
import { pnlSummary } from "../lib/pnl";

/**
 * Quant-terminal status ticker — a full-width monospace command line summarizing the live
 * state (NAV · PnL · regime · vlayer · Fear&Greed) with a blinking cursor. Reuses the same
 * snapshot data + format/pnl utils as the rest of the dashboard.
 */
export default function StatusTicker({ data, live }: { data: Snapshot; live: boolean }) {
  const navVal = data.nav?.current_nav ?? data.state?.nav ?? null;

  let pnlTxt = "—";
  try {
    const p = pnlSummary(data.nav, undefined, data.economy?.anchor_usd ?? null);
    pnlTxt = fmtSignedPct(p.netPct);
  } catch {
    /* keep placeholder */
  }

  const regime = data.regime?.fear_greed_label ?? "—";
  const fg = data.regime?.fear_greed;
  const prov = data.pillars?.commerce?.provenance;
  const vlayerState = prov?.attestation === "onchain" ? "proven" : prov?.enabled ? "pending" : "off";

  const cells: [string, string][] = [
    ["NAV", navVal != null ? fmtUsd(navVal) : "—"],
    ["PNL", pnlTxt],
    ["REGIME", regime],
    ["VLAYER", vlayerState],
    ["FG", fg != null ? String(fg) : "—"],
  ];

  return (
    <div className="glow-card overflow-x-auto px-3 py-2">
      <div className="flex items-center gap-x-4 gap-y-1 whitespace-nowrap font-mono text-[12px]">
        <span className="font-bold text-brand">mission-control</span>
        <span className="hidden text-muted sm:inline">~$ status --live</span>
        {cells.map(([k, v]) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <span className="text-muted">{k}</span>
            <span className="font-bold text-ink">{v}</span>
          </span>
        ))}
        <span className="ml-auto inline-flex items-center gap-1 pl-2 font-bold text-up">
          {live ? "live" : "demo"}
          <span className="term-cursor" aria-hidden />
        </span>
      </div>
    </div>
  );
}
