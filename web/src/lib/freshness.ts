// Single source of truth for "how old is the trading data the dashboard is showing".
//
// The dashboard mixes two provenance classes (see the data-integrity audit):
//   • Class A — recomputed live each request (wallet, market-intel, CMC probes). `live` (API responding)
//     is the right freshness signal for these.
//   • Class B — journal/seed-derived (NAV curve, rebalances, rationale, weights, economy, rotation …),
//     which only advance when the allocator ticks. For these, "the API responded" (`live`) is NOT the
//     same as "the data is fresh": a frozen tick under a green LIVE pill is the exact thing the audit flags.
//
// So Class-B panels gate their LIVE pill on the AGE OF THE LAST TICK, derived CLIENT-SIDE from the
// newest rebalance `ts` that is already inside the (edge-cached) snapshot. This adds zero server-side
// payload churn — it must NOT become a server `now()`-derived field, which would break the /api/snapshot
// ETag/304 + micro-cache (see SCALABILITY.md).

/** The contest arm rebalances daily (rebal_bars=6 × 4h candles = 24h); allow one cadence + 4h grace
 * before a tick reads as "stale" rather than "between rebalances". */
export const STALE_AFTER_S = 28 * 3600;

/** Seconds since the given ISO timestamp, or null if absent/unparseable. Clamped at 0. */
export function tickAgeSeconds(ts: string | null | undefined, now: number = Date.now()): number | null {
  if (!ts) return null;
  const t = new Date(ts).getTime();
  if (Number.isNaN(t)) return null;
  return Math.max(0, (now - t) / 1000);
}

/** True when the last tick is older than the rebalance cadence (or unknown). A null age is treated as
 * stale so we never claim "fresh" without a real tick to point at. */
export function isTickStale(ageS: number | null | undefined): boolean {
  return ageS == null || ageS > STALE_AFTER_S;
}

/** Compact age for a pill ("19h", "3m", "2d") — distinct from format.ageLabel which suffixes " ago". */
export function ageShort(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 129600) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}
