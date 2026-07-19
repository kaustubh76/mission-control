// Shared PnL / drawdown derivations so the hero NAV card and the detailed PnL card
// read from one source of truth instead of drifting.

import type { Nav } from "../api/types";

export interface Day {
  date: string;
  nav: number;
  pnl: number;
  pct: number;
}

/** End-of-day NAV per UTC day → day-over-day PnL. The "only matters at EOD" view. */
export function dailyPnl(curve: { ts: string; nav: number }[]): Day[] {
  if (curve.length === 0) return [];
  const eod = new Map<string, number>(); // date → last NAV that day
  for (const p of curve) eod.set(p.ts.slice(0, 10), p.nav);
  const out: Day[] = [];
  let prev = curve[0].nav;
  for (const [date, nav] of eod) {
    out.push({ date, nav, pnl: nav - prev, pct: prev ? nav / prev - 1 : 0 });
    prev = nav;
  }
  return out;
}

export interface PnlSummary {
  start: number;
  current: number;
  net: number;
  netPct: number;
  daily: Day[];
  today: Day | null;        // the most recent day in the series (NOT necessarily the current UTC day)
  latestDate: string | null; // UTC date (YYYY-MM-DD) of that most recent day
  latestIsToday: boolean;   // whether the latest day IS the current UTC day (else the card is stale)
  best: Day | null;
  worst: Day | null;
  wins: number;             // days the book finished up
  decided: number;          // days with a non-flat move (denominator)
  winRate: number | null;   // wins / decided; null when nothing has resolved yet
}

const EPS = 1e-9;

/** Current UTC calendar date (YYYY-MM-DD) in the viewer's browser — used to tell a fresh
 * "today" apart from a stale latest close. Injectable so the logic stays testable. */
export function utcToday(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

/** Net + per-day breakdown used by both PnLCard and the hero NAV stat.
 * `startOverride` lets callers anchor net/netPct to the contest campaign-start NAV (economy.anchor_usd)
 * so the P&L card reconciles with the Agent-economy card instead of measuring from the first curve point. */
export function pnlSummary(
  nav: Nav,
  todayUtc: string = utcToday(),
  startOverride?: number | null,
): PnlSummary {
  const curve = nav.curve ?? [];
  const start = startOverride != null ? startOverride : curve.length ? curve[0].nav : nav.current_nav ?? 0;
  const current = nav.current_nav ?? (curve.length ? curve[curve.length - 1].nav : start);
  const net = current - start;
  const netPct = start ? current / start - 1 : 0;
  const daily = dailyPnl(curve);
  const today = daily.length ? daily[daily.length - 1] : null;
  const latestDate = today?.date ?? null;
  // Only call it "today" when the most recent EOD point is actually the current UTC day —
  // otherwise a campaign that hasn't ticked since (say) 06-13 mislabels a days-old move as
  // "today". When stale, the card shows "latest close · <date>" instead of lying.
  const latestIsToday = latestDate != null && latestDate === todayUtc;
  const best = daily.reduce<Day | null>((m, d) => (!m || d.pnl > m.pnl ? d : m), null);
  const worst = daily.reduce<Day | null>((m, d) => (!m || d.pnl < m.pnl ? d : m), null);
  // Win rate over the SAME daily series the card plots, so the % reconciles with the
  // visible green/red bars. Flat days (the first day's 0-pnl baseline, untraded days)
  // are excluded from both numerator and denominator.
  const wins = daily.filter((d) => d.pnl > EPS).length;
  const decided = daily.filter((d) => Math.abs(d.pnl) > EPS).length;
  // Need ≥2 decided days for a win-rate to mean anything — a single closed day gives a meaningless
  // 0%/100%. Below that, surface null so the card shows "—" instead of a spurious rate.
  const winRate = decided >= 2 ? wins / decided : null;
  return {
    start,
    current,
    net,
    netPct,
    daily,
    today,
    latestDate,
    latestIsToday,
    best,
    worst,
    wins,
    decided,
    winRate,
  };
}

export type DdBand = "safe" | "caution" | "danger";

/** Which risk band the live drawdown sits in, relative to the team cap / DQ line. */
export function ddBand(dd: number, caps: Nav["caps"]): DdBand {
  if (dd >= caps.dq) return "danger";
  if (dd >= caps.team) return "caution";
  return "safe";
}

/** Plain-English headline for the live drawdown ("down 0.4% from its best — safe"). */
export function ddPlain(dd: number, caps: Nav["caps"]): string {
  const pct = (dd * 100).toFixed(1);
  switch (ddBand(dd, caps)) {
    case "danger":
      return `down ${pct}% from its best — past the ${(caps.team * 100).toFixed(0)}% cap`;
    case "caution":
      return `down ${pct}% from its best — easing off risk`;
    default:
      return dd <= 1e-4 ? "at a fresh high-water mark" : `down ${pct}% from its best — safe`;
  }
}
