import type { Scheduler } from "../api/types";
import Card from "./ui/Card";
import StatusPill, { type Tone } from "./ui/StatusPill";

const UP = "#16c784";
const DOWN = "#ea3943";

const age = (h: number | null, m: number | null): string =>
  h != null ? (h < 1 ? `${Math.round(h * 60)}m ago` : `${h}h ago`) : m != null ? `${m}m ago` : "never";

/**
 * Scheduler health — surfaces the age of the last live tick + the last intraday drawdown-watch run, so a
 * SILENTLY-dead cron (it once ran dark for days) shows up red within an hour instead of going unnoticed.
 */
export default function SchedulerCard({ sched }: { sched: Scheduler | null | undefined }) {
  if (!sched) {
    return (
      <Card label="Scheduler" accent="#7C5CFF">
        <div className="flex h-16 items-center justify-center text-xs text-muted">no scheduler data</div>
      </Card>
    );
  }
  const tone: Tone = sched.ok ? "up" : "down";
  const row = (label: string, ageText: string, stale: boolean) => (
    <div className="flex items-baseline justify-between">
      <span className="text-muted">{label}</span>
      <span className="font-mono" style={{ color: stale ? DOWN : UP }}>
        {ageText} {stale ? "✗" : "✓"}
      </span>
    </div>
  );
  return (
    <Card
      label="Scheduler"
      accent="#7C5CFF"
      right={
        <StatusPill tone={tone} dot srText={`scheduler ${sched.ok ? "healthy" : "stale"}`}>
          {sched.ok ? "HEALTHY" : "STALE"}
        </StatusPill>
      }
    >
      <div className="space-y-1 text-xs">
        {row("live tick", age(sched.live_tick_age_h, null), sched.live_tick_stale)}
        {row("dd-watch (risk halt)", age(null, sched.dd_watch_age_m), sched.dd_watch_stale)}
        {sched.note && <div className="mt-1 text-[11px] leading-snug text-muted">{sched.note}</div>}
      </div>
    </Card>
  );
}
