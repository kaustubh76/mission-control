import { getNav } from "../api/client";
import type { Nav, State } from "../api/types";
import { fmtSignedPct, fmtUsd } from "../lib/format";
import { isTickStale, tickAgeSeconds } from "../lib/freshness";
import { ddBand } from "../lib/pnl";
import AnimatedNumber from "./ui/AnimatedNumber";
import Card from "./ui/Card";
import CheckButton from "./ui/CheckButton";
import Sparkline from "./ui/Sparkline";
import StatusPill from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

const SAFE = "#16c784";
const CAUTION = "#f0b90b";
const BREACH = "#ea3943";

function bandColor(b: ReturnType<typeof ddBand>): string {
  return b === "danger" ? BREACH : b === "caution" ? CAUTION : SAFE;
}

/** Self-test: re-read NAV and verdict the drawdown against the caps (mirrors the
 * SystemDiagnostics risk probe). Read-only — never trades. */
async function riskCheck() {
  const nv = await getNav();
  const dd = nv.drawdown.current;
  const pct = `${(dd * 100).toFixed(1)}%`;
  if (dd >= nv.caps.dq) return { ok: false, detail: `${pct} ≥ DQ ${(nv.caps.dq * 100).toFixed(0)}%` };
  if (dd >= nv.caps.team) return { ok: false, detail: `${pct} ≥ team cap ${(nv.caps.team * 100).toFixed(0)}%` };
  return { ok: true, detail: `drawdown ${pct} · safe` };
}

function DrawdownGauge({ dd, caps }: { dd: number; caps: Nav["caps"] }) {
  // Scale the bar so the DQ cap (30%) sits near the right edge with headroom.
  const max = Math.max(caps.dq * 1.15, dd * 1.1, 0.01);
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  const color = bandColor(ddBand(dd, caps));
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between text-[11px] text-muted">
        <span className="flex items-center gap-1">
          drawdown from high-water <InfoTip term="drawdown" />
        </span>
        <span className="font-mono" style={{ color }}>
          {/* rising drawdown is BAD → flashInvert pulses red on a rise, green on recovery */}
          <AnimatedNumber value={dd} format={(n) => `${(n * 100).toFixed(1)}%`} flash flashInvert />
        </span>
      </div>
      <div className="relative h-2.5 w-full rounded-full bg-edge">
        <div
          className="absolute inset-y-0 left-0 rounded-full transition-all duration-700"
          style={{ width: pct(dd), background: color }}
        />
        {/* team (15%) + DQ (30%) markers */}
        <Marker at={pct(caps.team)} color={CAUTION} label="15%" />
        <Marker at={pct(caps.dq)} color={BREACH} label="30%" />
      </div>
    </div>
  );
}

function Marker({ at, color, label }: { at: string; color: string; label: string }) {
  return (
    <div className="absolute -top-0.5 flex flex-col items-center" style={{ left: at }}>
      <span className="h-3.5 w-px" style={{ background: color }} />
      <span className="mt-1 text-[9px]" style={{ color }}>
        {label}
      </span>
    </div>
  );
}

/** Risk card — the "refuses to lose big" story: live drawdown vs the team cap & DQ line. */
export default function NavCard({
  nav,
  state,
  live = true,
  lastTickTs,
}: {
  nav: Nav;
  state: State;
  live?: boolean;
  lastTickTs?: string | null;
}) {
  // The drawdown/HWM here are journal-derived (Class B) — only pulse the band pill as a "live" signal
  // when the trading data has actually ticked within cadence, not merely because the API answered.
  const fresh = live && !isTickStale(tickAgeSeconds(lastTickTs));
  const navValue = nav.current_nav ?? state.nav ?? 0;
  const start = nav.curve.length ? nav.curve[0].nav : navValue;
  const ret = start ? navValue / start - 1 : 0;
  const retColor = ret > 0 ? SAFE : ret < 0 ? BREACH : "#8a8f9c";
  const band = ddBand(nav.drawdown.current, nav.caps);
  const bandLabel = band === "danger" ? "BREACH" : band === "caution" ? "CAUTION" : "SAFE";
  const bandTone = band === "danger" ? "down" : band === "caution" ? "warn" : "up";
  const ddSeries = (nav.drawdown.series ?? []).map((p) => p.dd);

  return (
    <Card
      label="Risk · Drawdown vs Limits"
      accent={bandColor(band)}
      className="flex h-full flex-col"
      right={
        <span className="flex items-center gap-2">
          <CheckButton label="re-check risk" run={riskCheck} disabled={!live} />
          <StatusPill tone={bandTone} dot pulse={fresh} srText={`drawdown ${band}`}>
            {bandLabel}
          </StatusPill>
        </span>
      }
    >
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="flex items-center gap-1 text-muted">
          high-water mark <InfoTip term="hwm" />
        </span>
        <span className="font-mono text-sub">
          {nav.hwm != null ? <AnimatedNumber value={nav.hwm} format={(n) => fmtUsd(n)} flash /> : "—"}
        </span>
      </div>
      <div className="mt-1.5 flex items-baseline justify-between gap-3 text-sm">
        <span className="text-muted">return since start</span>
        <span className="font-mono font-semibold" style={{ color: retColor }}>
          <AnimatedNumber value={ret} format={(n) => fmtSignedPct(n)} flash />
        </span>
      </div>

      {ddSeries.length > 1 && (
        <div className="mt-2 flex items-center gap-2">
          <span className="shrink-0 text-[9px] uppercase tracking-wider text-muted">dd trend</span>
          <Sparkline data={ddSeries} color={bandColor(band)} height={32} baseline={0} padFrac={0.15} className="flex-1" />
        </div>
      )}

      <DrawdownGauge dd={nav.drawdown.current} caps={nav.caps} />

      <div className="mt-4 flex items-center justify-between text-[10px] text-muted">
        <span className="flex items-center gap-1">
          team cap 15% <InfoTip term="teamCap" />
        </span>
        <span className="flex items-center gap-1">
          DQ line 30% <InfoTip term="dqLine" />
        </span>
      </div>

      {state.halted && (
        <div className="mt-3 rounded-sm border border-danger/40 bg-danger/10 px-2 py-1 text-[11px] text-danger">
          ⚠ drawdown halt active — agent flat, not trading
        </div>
      )}
    </Card>
  );
}
