import { useNow } from "../../hooks/useNow";
import { ageLabel } from "../../lib/format";
import { ageShort, isTickStale, tickAgeSeconds } from "../../lib/freshness";
import StatusPill from "./StatusPill";

/**
 * The honest freshness pill for Class-B (journal/seed-derived) panels — NAV curve, weights, rotation,
 * P&L, etc. Three states, so a frozen tick never wears a green "LIVE":
 *   • not live (static snapshot fallback) → neutral "SNAPSHOT"
 *   • live + tick within cadence          → green pulsing "{liveLabel}"
 *   • live + tick older than cadence       → amber, no pulse, "{liveLabel} · {age}" (connection live, data delayed)
 *
 * Age is derived CLIENT-SIDE from the newest rebalance ts already in the snapshot (no server churn —
 * see lib/freshness.ts). Re-ticks every second so the age stays honest without a re-poll.
 */
export default function FreshnessPill({
  live,
  lastTickTs,
  liveLabel = "LIVE",
  srLive = "live data",
}: {
  live: boolean;
  lastTickTs: string | null | undefined;
  liveLabel?: string;
  srLive?: string;
}) {
  const now = useNow(1000);
  if (!live) {
    return (
      <StatusPill tone="neutral" srText="static snapshot — live API unreachable">
        SNAPSHOT
      </StatusPill>
    );
  }
  const ageS = tickAgeSeconds(lastTickTs, now);
  if (!isTickStale(ageS)) {
    return (
      <StatusPill tone="up" dot pulse srText={srLive}>
        {liveLabel}
      </StatusPill>
    );
  }
  // Live connection, but the trading data hasn't ticked within the rebalance cadence — show the age
  // instead of a green pulse so it never reads as a fresh value.
  return (
    <StatusPill tone="warn" dot srText={`live connection — data last ticked ${ageLabel(ageS)}`}>
      {liveLabel} · {ageShort(ageS)}
    </StatusPill>
  );
}
