import type { LiveArm } from "../api/types";
import Card from "./ui/Card";
import StatusPill, { type Tone } from "./ui/StatusPill";

/**
 * Live arm — the strategy actually trading real money (or configured to), with its survival/forward
 * GATE. Distinct from the SIM Strategy Lab: this is the ONE arm the LIVE/contest tick runs, derived
 * env-independently (live journal → STRATEGY_NAME → default) and operator-switchable via
 * `live_tick.sh <arm>`. The dashboard cannot change it (contest-safety).
 */
export default function LiveArmCard({ liveArm }: { liveArm: LiveArm | null | undefined }) {
  if (!liveArm) {
    return (
      <Card label="Live arm" accent="#16c784">
        <div className="flex h-20 items-center justify-center text-xs text-muted">live arm unavailable</div>
      </Card>
    );
  }
  const sv = liveArm.survival;
  const pass = sv?.passed;
  const survTone: Tone = pass ? "up" : pass === false ? "down" : "neutral";
  const dd = sv?.worst_week_dd != null ? `${(sv.worst_week_dd * 100).toFixed(1)}% DD` : "";
  const tpw = sv?.trades_per_week != null ? `${sv.trades_per_week.toFixed(0)}/wk` : "";
  const fwdElig = liveArm.forward?.forward_eligible;
  const fwdTone: Tone = fwdElig ? "up" : "warn";

  return (
    <Card
      label="Live arm"
      accent="#16c784"
      right={
        <StatusPill tone="up" dot srText={`live contest arm: ${liveArm.name}`}>
          🔒 LIVE
        </StatusPill>
      }
    >
      <div className="space-y-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-display text-sm font-bold text-ink">{liveArm.name}</span>
          {liveArm.candle_source && (
            <span className="rounded-sm bg-panel2 px-1.5 py-0.5 text-[10px] text-sub">
              {liveArm.candle_source === "cmc_4h" ? "CMC-native" : liveArm.candle_source}
            </span>
          )}
        </div>

        {/* The GATE: survival (hard DQ rail) + forward-promotion status. */}
        <div className="flex flex-wrap items-center gap-2 border-t border-edge pt-2 text-[11px]">
          <span className="text-muted">survival</span>
          <StatusPill tone={survTone}>{pass ? "PASS" : pass === false ? "FAIL" : "—"}</StatusPill>
          {(dd || tpw) && <span className="font-mono text-[9px] text-muted">{[dd, tpw].filter(Boolean).join(" · ")}</span>}
          <StatusPill tone={fwdTone}>{fwdElig ? "forward eligible" : "forward accruing"}</StatusPill>
          {sv?.ts && (
            // Date-stamp the static backtest verdict so it never reads as a live performance figure.
            <span className="ml-auto font-mono text-[9px] text-muted">backtest as of {sv.ts.slice(0, 10)}</span>
          )}
        </div>

        <div className="text-[10.5px] leading-relaxed text-muted">
          source: <span className="text-sub">{liveArm.source}</span>
          {liveArm.note ? ` · ${liveArm.note}` : ""}
        </div>
      </div>
    </Card>
  );
}
