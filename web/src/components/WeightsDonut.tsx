import { useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";

import type { State } from "../api/types";
import { fmtPct, fmtUsd, tokenColor } from "../lib/format";
import AnimatedNumber from "./ui/AnimatedNumber";
import Card from "./ui/Card";
import FreshnessPill from "./ui/FreshnessPill";
import InfoTip from "./ui/Tooltip";

/** Donut + legend body (no Card chrome). Reused by the standalone WeightsDonut and folded into the
 * Real-Funds wallet card so portfolio weights sit right next to the on-chain balance. */
export function WeightsBody({ state, nav }: { state: State; nav: number }) {
  // Prefer the post-rebalance target weights; fold the implicit cash remainder
  // into a USDT slice so the donut always sums to 100%.
  const w = { ...state.weights };
  const deployed = Object.entries(w)
    .filter(([k]) => k !== "USDT")
    .reduce((s, [, v]) => s + v, 0);
  const cash = Math.max(0, 1 - deployed);
  if (cash > 1e-4) w.USDT = (w.USDT ?? 0) + cash;

  const slices = Object.entries(w)
    .filter(([, v]) => v > 1e-4)
    .sort((a, b) => b[1] - a[1])
    .map(([name, value]) => ({ name, value }));

  const deployedPct = 1 - (w.USDT ?? 0);
  const [hover, setHover] = useState<string | null>(null);
  const hovered = hover ? slices.find((s) => s.name === hover) : null;

  if (slices.length === 0) {
    return <div className="py-6 text-center text-xs text-muted">all in cash — no tokens held yet</div>;
  }

  return (
    <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:gap-4">
      <div className="relative h-[140px] w-[140px] shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="name"
              innerRadius={46}
              outerRadius={66}
              paddingAngle={2}
              stroke="none"
              isAnimationActive
              onMouseEnter={(_, i) => setHover(slices[i]?.name ?? null)}
              onMouseLeave={() => setHover(null)}
            >
              {slices.map((s) => (
                <Cell
                  key={s.name}
                  fill={tokenColor(s.name)}
                  opacity={hover && hover !== s.name ? 0.35 : 1}
                  style={{ cursor: "pointer", transition: "opacity 0.15s" }}
                />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          {hovered ? (
            <>
              <span className="font-display text-sm font-bold" style={{ color: tokenColor(hovered.name) }}>
                {hovered.name}
              </span>
              <span className="font-mono text-xs text-ink">{fmtPct(hovered.value, 1)}</span>
              {nav > 0 && (
                <span className="font-mono text-[10px] text-muted">{fmtUsd(hovered.value * nav)}</span>
              )}
            </>
          ) : (
            <>
              <span className="text-[10px] uppercase tracking-wider text-muted">deployed</span>
              <span className="font-display text-lg font-bold text-neon tabular-nums">
                <AnimatedNumber value={deployedPct * 100} format={(n) => fmtPct(n / 100, 0)} flash />
              </span>
            </>
          )}
        </div>
      </div>
      <ul className="flex-1 space-y-1.5 text-xs">
        {slices.map((s) => (
          <li
            key={s.name}
            onMouseEnter={() => setHover(s.name)}
            onMouseLeave={() => setHover(null)}
            className={`flex cursor-default items-center justify-between rounded-sm px-1 py-0.5 transition ${
              hover === s.name ? "bg-panel2" : ""
            } ${hover && hover !== s.name ? "opacity-50" : ""}`}
          >
            <span className="flex items-center gap-2">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: tokenColor(s.name) }} />
              <span className="text-ink">{s.name}</span>
            </span>
            <span className="font-mono text-sub">
              {hover === s.name && nav > 0 ? `${fmtUsd(s.value * nav)} · ` : ""}
              {fmtPct(s.value, 1)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function WeightsDonut({
  state,
  live = true,
  lastTickTs,
}: {
  state: State;
  live?: boolean;
  lastTickTs?: string | null;
}) {
  const right = (
    <span className="flex items-center gap-1.5">
      <InfoTip term="deployed" />
      <FreshnessPill live={live} lastTickTs={lastTickTs} srLive="live target weights" />
    </span>
  );
  return (
    <Card label="Portfolio Weights" accent="#7C5CFF" className="flex h-full flex-col" right={right}>
      <WeightsBody state={state} nav={state.nav ?? 0} />
    </Card>
  );
}
