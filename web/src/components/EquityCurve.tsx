import { useState } from "react";
import {
  Area,
  AreaChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { Nav } from "../api/types";
import { candleSourceLabel, fmtSignedPct, fmtUsd } from "../lib/format";
import AnimatedNumber from "./ui/AnimatedNumber";
import Card from "./ui/Card";
import FreshnessPill from "./ui/FreshnessPill";

type Range = "all" | "7d";

export default function EquityCurve({
  nav,
  live = true,
  lastTickTs,
  candleSource,
}: {
  nav: Nav;
  live?: boolean;
  lastTickTs?: string | null;
  candleSource?: string | null;
}) {
  const [range, setRange] = useState<Range>("all");
  const curve = nav.curve ?? [];
  // Offer the 7D cut only when there's actually more than 7 days of history.
  const spanMs = curve.length > 1 ? +new Date(curve[curve.length - 1].ts) - +new Date(curve[0].ts) : 0;
  const has7d = spanMs > 7 * 86400_000;
  const cutoff = Date.now() - 7 * 86400_000;
  const shown = range === "7d" && has7d ? curve.filter((p) => +new Date(p.ts) >= cutoff) : curve;

  const data = shown.map((p) => ({
    t: new Date(p.ts).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
    }),
    nav: p.nav,
  }));

  const navs = data.map((d) => d.nav);
  const lo = navs.length ? Math.min(...navs) : 0;
  const hi = navs.length ? Math.max(...navs) : 1;
  const pad = Math.max((hi - lo) * 0.15, hi * 0.002, 1);

  // Tangible endpoint readout: where the curve is now, and how far from where it started.
  const latestNav = shown.length ? shown[shown.length - 1].nav : null;
  const firstNav = shown.length ? shown[0].nav : null;
  const pct = firstNav && firstNav !== 0 && latestNav != null ? (latestNav - firstNav) / firstNav : null;

  return (
    <Card
      label="Equity Curve"
      accent="#3861fb"
      className="flex h-full flex-col"
      right={
        <span className="flex items-center gap-2 text-[11px] text-muted">
          <FreshnessPill live={live} lastTickTs={lastTickTs} srLive="live NAV curve" />
          {has7d && (
            <span className="flex overflow-hidden rounded-sm border border-edge font-mono text-[10px]">
              {(["all", "7d"] as const).map((rk) => (
                <button
                  key={rk}
                  onClick={() => setRange(rk)}
                  aria-pressed={range === rk}
                  className={`px-2 py-0.5 uppercase transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/50 ${
                    range === rk ? "bg-cool/20 text-cyan" : "text-muted hover:text-sub"
                  }`}
                >
                  {rk}
                </button>
              ))}
            </span>
          )}
          {data.length} rebalances
        </span>
      }
    >
      {data.length === 0 ? (
        <Empty />
      ) : (
        <>
        {latestNav != null && (
          <div className="mb-2 flex items-baseline gap-1.5 font-mono text-[11px] text-muted">
            <span className="uppercase tracking-wider">latest</span>
            <span className="font-display text-sm font-bold text-ink tabular-nums">
              <AnimatedNumber value={latestNav} format={(n) => fmtUsd(n)} flash />
            </span>
            {pct != null && (
              <span
                className="font-bold tabular-nums"
                style={{ color: pct >= 0 ? "#16c784" : "#ea3943" }}
              >
                {fmtSignedPct(pct)}
              </span>
            )}
            <span>since start</span>
            {candleSourceLabel(candleSource) && (
              <span className="ml-auto text-cyan/80">· priced on {candleSourceLabel(candleSource)}</span>
            )}
          </div>
        )}
        <div className="h-[150px] w-full sm:h-[180px] md:h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
              <defs>
                <linearGradient id="navFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#16c784" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#16c784" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="t"
                tick={{ fill: "#8a8f9c", fontSize: 10 }}
                tickLine={false}
                axisLine={{ stroke: "#2c2f3a" }}
                minTickGap={28}
              />
              <YAxis
                domain={[lo - pad, hi + pad]}
                tick={{ fill: "#8a8f9c", fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                width={56}
                tickFormatter={(v) => fmtUsd(v, 0)}
              />
              {nav.hwm != null && (
                <ReferenceLine
                  y={nav.hwm}
                  stroke="#3a3d46"
                  strokeDasharray="4 4"
                  label={{ value: "HWM", position: "insideTopRight", fontSize: 9, fill: "#8a8f9c" }}
                />
              )}
              <Tooltip
                contentStyle={{
                  background: "#16181f",
                  border: "1px solid #2c2f3a",
                  borderRadius: 2,
                  fontSize: 12,
                }}
                labelStyle={{ color: "#c5c8d0" }}
                formatter={(v: number) => [fmtUsd(v), "NAV"]}
              />
              <Area
                type="monotone"
                dataKey="nav"
                stroke="#16c784"
                strokeWidth={2}
                fill="url(#navFill)"
                dot={{ r: 2, fill: "#16c784" }}
                isAnimationActive
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        </>
      )}
    </Card>
  );
}

function Empty() {
  return (
    <div className="flex h-24 items-center justify-center text-center text-xs text-muted">
      no rebalances yet — waiting for the first live rebalance
    </div>
  );
}
