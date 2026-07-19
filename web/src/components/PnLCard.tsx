import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis } from "recharts";

import type { Nav } from "../api/types";
import { fmtSignedPct, fmtUsd } from "../lib/format";
import { pnlSummary } from "../lib/pnl";
import AnimatedNumber from "./ui/AnimatedNumber";
import Card from "./ui/Card";
import FreshnessPill from "./ui/FreshnessPill";
import Stat from "./ui/Stat";

const UP = "#16c784";
const DOWN = "#ea3943";
const FLAT = "#8a8f9c";
const pnlColor = (v: number) => (v > 0 ? UP : v < 0 ? DOWN : FLAT);
const dir = (v: number) => (v > 0 ? "up" : v < 0 ? "down" : "flat") as "up" | "down" | "flat";
const signedUsd = (v: number) => `${v >= 0 ? "+" : "−"}${fmtUsd(Math.abs(v))}`;

function BarTip({ active, payload }: { active?: boolean; payload?: Array<{ payload: { date: string; pnl: number } }> }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-sm border border-edge bg-panel2 px-2 py-1 text-[11px]">
      <div className="text-muted">{d.date}</div>
      <div style={{ color: pnlColor(d.pnl) }}>{signedUsd(d.pnl)}</div>
    </div>
  );
}

export default function PnLCard({
  nav,
  live = true,
  lastTickTs,
  anchorUsd,
}: {
  nav: Nav;
  live?: boolean;
  lastTickTs?: string | null;
  anchorUsd?: number | null;
}) {
  // Anchor net P&L to the campaign-start NAV (economy.anchor_usd) so this card reconciles with the
  // Agent-economy card's trading PnL instead of measuring from the first curve point.
  const { net, netPct, daily, today, latestIsToday, best, worst, wins, decided, winRate } =
    pnlSummary(nav, undefined, anchorUsd);
  const bars = daily.map((d) => ({ date: d.date.slice(5), pnl: Number(d.pnl.toFixed(2)) }));
  const winColor = winRate == null ? FLAT : winRate >= 0.5 ? UP : DOWN;

  return (
    <Card
      label="Profit & Loss"
      accent={pnlColor(net)}
      className="flex h-full flex-col"
      right={
        <span className="flex items-center gap-2">
          <FreshnessPill live={live} lastTickTs={lastTickTs} liveLabel="LIVE" srLive="live on-chain book P&L" />
          <span className="text-[11px] text-muted">
            EOD · {daily.length} day{daily.length === 1 ? "" : "s"}
          </span>
        </span>
      }
    >
      <div className="mb-3 text-[10px] leading-snug text-muted/70">
        cumulative performance + daily accuracy
      </div>
      <div className="flex flex-col gap-5 lg:flex-row lg:items-center">
        <div className="flex shrink-0 gap-6">
          <Stat
            label="net pnl"
            term="paperBook"
            size="md"
            color={pnlColor(net)}
            glow
            value={<AnimatedNumber value={net} format={signedUsd} flash />}
            delta={{ text: fmtSignedPct(netPct), dir: dir(net) }}
            plain="since the agent started"
          />
          <Stat
            label={latestIsToday ? "today" : "latest"}
            size="md"
            color={pnlColor(today?.pnl ?? 0)}
            value={today ? <AnimatedNumber value={today.pnl} format={signedUsd} flash /> : "—"}
            delta={today ? { text: fmtSignedPct(today.pct), dir: dir(today.pnl) } : undefined}
            plain={
              !today
                ? "no closed day yet"
                : latestIsToday
                  ? "end-of-day move"
                  : `latest close · ${today.date.slice(5)} (awaiting today's tick)`
            }
          />
          <Stat
            label="win rate"
            term="winRate"
            size="md"
            color={winColor}
            glow
            value={winRate == null ? "—" : <AnimatedNumber value={winRate * 100} format={(n) => `${Math.round(n)}%`} flash />}
            plain={winRate != null ? `${wins} of ${decided} days up` : "needs ≥2 closed days"}
          />
        </div>

        <div className="h-24 min-w-0 flex-1">
          {bars.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={bars} margin={{ top: 6, right: 4, bottom: 0, left: 4 }}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: "#8a8f9c" }}
                  axisLine={false}
                  tickLine={false}
                  interval="preserveStartEnd"
                  minTickGap={14}
                />
                <Tooltip cursor={{ fill: "#2c2f3a55" }} content={<BarTip />} />
                <Bar dataKey="pnl" radius={[2, 2, 0, 0]} isAnimationActive>
                  {bars.map((b, i) => (
                    <Cell key={i} fill={pnlColor(b.pnl)} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            // No full-day EOD close yet — fill the space with the running cumulative figure
            // + a note on when the daily bars start, instead of a bare "no history" line.
            <div className="flex h-full flex-col items-center justify-center gap-0.5 text-center">
              <div className="font-mono text-sm" style={{ color: pnlColor(net) }}>
                {signedUsd(net)} <span className="text-muted">since start</span>
              </div>
              <div className="max-w-[18rem] text-[11px] leading-snug text-muted">
                daily P&amp;L bars build after the first full UTC close
              </div>
            </div>
          )}
        </div>

        {best && worst && daily.length >= 1 && (
          <div className="flex shrink-0 gap-6 text-[11px] lg:flex-col lg:gap-1">
            <div>
              <span className="text-muted">best day </span>
              <span style={{ color: UP }}>{signedUsd(best.pnl)}</span>
              <span className="text-muted"> {best.date.slice(5)}</span>
            </div>
            <div>
              <span className="text-muted">worst day </span>
              <span style={{ color: DOWN }}>{signedUsd(worst.pnl)}</span>
              <span className="text-muted"> {worst.date.slice(5)}</span>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
