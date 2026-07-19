import { useReducedMotion } from "framer-motion";

import { useNow } from "../hooks/useNow";

const R = 6;
const C = 2 * Math.PI * R;
const GREEN = "#16c784";

/**
 * A tiny poll-synced ring beside the connection pill: it fills over each 4s poll
 * cycle and pings the instant fresh data lands, so the dashboard visibly "breathes."
 * Purely decorative (aria-hidden — the "updated Ns ago" text carries the meaning).
 * Only animates when genuinely live; on the demo snapshot / offline / reduced-motion
 * it degrades to a static dot so it never fakes a cadence.
 */
export default function HeartbeatRing({
  lastUpdated,
  intervalMs = 4000,
  live,
  error,
}: {
  lastUpdated: number | null;
  intervalMs?: number;
  live: boolean;
  error: string | null;
}) {
  const reduce = useReducedMotion();
  const now = useNow(250);
  const animate = live && !error && !reduce && lastUpdated != null;

  if (!animate) {
    const color = error ? "#ea3943" : live ? GREEN : "#8a8f9c";
    return <span aria-hidden className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />;
  }

  const progress = Math.max(0, Math.min(1, (now - (lastUpdated as number)) / intervalMs));
  const offset = C * (1 - progress);
  return (
    <span aria-hidden className="relative inline-flex h-4 w-4 items-center justify-center">
      <span
        key={lastUpdated as number}
        className="mc-heartbeat-ping absolute inset-0 rounded-full"
        style={{ border: `1.5px solid ${GREEN}` }}
      />
      <svg width={16} height={16} viewBox="0 0 16 16" className="-rotate-90">
        <circle cx="8" cy="8" r={R} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-edge" opacity={0.5} />
        <circle
          cx="8"
          cy="8"
          r={R}
          fill="none"
          stroke={GREEN}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeDasharray={C}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 0.2s linear" }}
        />
      </svg>
    </span>
  );
}
