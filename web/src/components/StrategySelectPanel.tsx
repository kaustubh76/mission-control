import type { StrategyMenuItem } from "../api/types";
import type { UseAllocator } from "../hooks/useAllocator";
import { ageLabel, fmtPct, fmtSignedPct } from "../lib/format";
import Card from "./ui/Card";
import Collapsible from "./ui/Collapsible";
import StatusPill, { type Tone } from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

/** Short calendar date ("Jun 16") for the backtest-validation provenance stamp. */
const fmtDate = (ts: string): string => {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

/**
 * Strategy Lab — a READ-ONLY, faithful render of the playbook §11 table. Each registered arm is a row
 * carrying its readiness · GATE (survival) · stability · forward · SCOREBOARD (backtest return + window
 * win-rate) — REAL backtest validation, the edge proof. The 🔒 LIVE/contest arm is operator-controlled
 * (live_tick.sh); the dashboard surfaces it but never switches it.
 *
 * Framing (load-bearing, per the playbook): SURVIVAL is the GATE (hard pass/fail); PnL &
 * win-rate are a SCOREBOARD over survivors, never an edge claim — so scoreboard numbers are
 * rendered in neutral tone, never green/red. Aliases (BNB_STRATEGY_0X) are hidden; they map
 * to the same canonical arm.
 */
function readinessView(r: StrategyMenuItem["readiness"]): { label: string; tone: Tone; title?: string } {
  switch (r?.state) {
    case "ready":
      return { label: "READY", tone: "up", title: r.note };
    case "not_ready":
      return { label: "NOT READY", tone: "down", title: r.note };
    case "incumbent":
      return { label: "🔒 LIVE", tone: "violet", title: r.note };
    case "in_progress":
      return { label: r.note?.includes("accruing") ? "ACCRUING" : "IN PROGRESS", tone: "warn", title: r.note };
    default:
      return { label: "—", tone: "neutral" };
  }
}

function survivalView(s: StrategyMenuItem["survival"]) {
  if (!s || s.passed === undefined) return { tone: "neutral" as Tone, label: "—", detail: "" };
  const dd = s.worst_week_dd != null ? `${(s.worst_week_dd * 100).toFixed(1)}% DD` : "";
  const tpw = s.trades_per_week != null ? `${s.trades_per_week.toFixed(0)}/wk` : "";
  return {
    tone: (s.passed ? "up" : "down") as Tone,
    label: s.passed ? "PASS" : "FAIL",
    detail: [dd, tpw].filter(Boolean).join(" · "),
  };
}

function stabilityView(stab: StrategyMenuItem["stability"]): { label: string; tone: Tone } {
  const g = stab?.grade;
  if (!g) return { label: "—", tone: "neutral" };
  if (g === "ROBUST") return { label: "ROBUST", tone: "up" };
  if (g === "FRAGILE") return { label: "FRAGILE", tone: "warn" };
  return { label: "UNSTABLE", tone: "down" };
}

function forwardView(f: StrategyMenuItem["forward"]): { label: string; tone: Tone } {
  if (!f || !f.status) return { label: "—", tone: "neutral" };
  if (f.status !== "evaluated") return { label: "accruing", tone: "neutral" };
  return f.forward_eligible ? { label: "eligible", tone: "up" } : { label: "not yet", tone: "warn" };
}

const btRet = (sb: StrategyMenuItem["scoreboard"]) =>
  sb?.total_return != null ? fmtSignedPct(sb.total_return, 0) : "—";
const winWindow = (sb: StrategyMenuItem["scoreboard"]) =>
  sb?.win_rate != null ? fmtPct(sb.win_rate, 0) : "—";

export default function StrategySelectPanel({ allocator }: { allocator: UseAllocator }) {
  const { data, live } = allocator;

  // §11 shows the canonical arms — hide BNB_STRATEGY_0X aliases (same underlying arm).
  const allItems = data?.strategies?.items ?? [];
  const arms = allItems.filter((s) => !s.alias_of);
  const liveArmName = data?.live_arm?.name; // the REAL live/contest arm (🔒) — read-only here
  const challengers = arms.filter((s) => s.readiness?.state !== "incumbent");
  const readyN = challengers.filter((s) => s.readiness?.state === "ready").length;
  // The live/contest arm gets an always-visible summary; the full per-arm table collapses below it.
  const liveArm = arms.find((s) => s.readiness?.state === "incumbent");
  const liveSv = liveArm ? survivalView(liveArm.survival) : null;

  // Backtest PROVENANCE — these verdicts are static artifacts from a `make refresh_dashboard` run, not live
  // metrics. Surface WHEN they were computed (the freshest verdict ts across arms) so the table doesn't read
  // as current at a glance. ISO timestamps compare lexicographically, so a string max == the latest.
  const validatedAt =
    arms
      .flatMap((s) => [s.survival?.ts, s.scoreboard?.ts, s.forward?.ts, s.stability?.ts])
      .filter((t): t is string => !!t)
      .reduce<string | null>((latest, t) => (latest == null || t > latest ? t : latest), null);
  const validatedAgeS = validatedAt ? Math.max(0, (Date.now() - new Date(validatedAt).getTime()) / 1000) : null;
  const validationStale = validatedAgeS != null && validatedAgeS > 14 * 86400; // backtests don't expire, but >2wk → nudge a re-run

  if (arms.length === 0) {
    return (
      <Card label="Strategy Lab" accent="#3861fb">
        <div className="text-[12px] text-muted">strategy registry unavailable</div>
      </Card>
    );
  }

  return (
    <Card
      label="Strategy Lab"
      accent="#3861fb"
      right={
        <span className="flex flex-wrap items-center gap-2">
          {validatedAt && (
            <span
              className={`font-mono text-[10px] ${validationStale ? "text-amber" : "text-muted"}`}
              title={`backtest gates last computed ${validatedAt} — static until the next \`make refresh_dashboard\``}
            >
              validated {ageLabel(validatedAgeS)} · {fmtDate(validatedAt)}
            </span>
          )}
          <StatusPill tone={readyN > 0 ? "up" : "neutral"} srText={`${readyN} of ${challengers.length} challenger arms ready`}>
            {readyN}/{challengers.length} READY
          </StatusPill>
        </span>
      }
    >
      {/* Always visible: the live/contest arm summary. */}
      {liveArm && liveSv ? (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-sm border border-violet/40 bg-violet/10 px-2.5 py-2">
          <StatusPill tone="violet">🔒 LIVE</StatusPill>
          <span className="font-display text-sm font-bold text-ink">{liveArm.name}</span>
          <span className="flex items-center gap-1 text-[10px] text-muted">
            gate <StatusPill tone={liveSv.tone}>{liveSv.label}</StatusPill>
          </span>
          {liveSv.detail && <span className="font-mono text-[9px] text-muted">{liveSv.detail}</span>}
          <span className="ml-auto font-mono text-[11px] text-sub">
            bt {btRet(liveArm.scoreboard)} · win {winWindow(liveArm.scoreboard)}
          </span>
        </div>
      ) : liveArmName ? (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-sm border border-violet/40 bg-violet/10 px-2.5 py-2">
          <StatusPill tone="violet">🔒 LIVE</StatusPill>
          <span className="font-display text-sm font-bold text-ink">{liveArmName}</span>
          <span className="text-[10px] text-muted">operator-controlled · validation below</span>
        </div>
      ) : null}

      {/* Everything else (banners, full per-arm table, footer) collapses behind a dropdown. */}
      <Collapsible title="full validation table" id="strategy-lab-detail" defaultOpen={false}>
      <div className="mb-2 flex items-center gap-1 text-[10px] leading-snug text-muted/80">
        <span>
          Survival is the <span className="text-sub">GATE</span> (pass/fail) · PnL &amp; win-rate are a{" "}
          <span className="text-sub">SCOREBOARD</span> over survivors — not an edge claim
        </span>
        <InfoTip term="scoreboard" />
      </div>

      <div className="mb-3 rounded-sm border border-cyan/30 bg-cyan/5 px-2 py-1 text-[11px] text-cyan">
        Read-only backtest validation — survival + stability + forward gates per arm. The 🔒 LIVE
        arm is operator-controlled (live_tick.sh); the dashboard never switches it.
      </div>
      {!live && (
        <div className="mb-3 rounded-sm border border-amber/40 bg-amber/10 px-2 py-1 text-[11px] text-amber">
          cached snapshot — reconnecting to the live API
        </div>
      )}

      {/* Desktop: the §11 table; rows are selectable (SIM) */}
      <div className="-mx-1 hidden overflow-x-auto md:block">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-muted">
              <th className="px-1 pb-2 font-medium">arm</th>
              <th className="px-1 pb-2 font-medium">
                <span className="inline-flex items-center gap-1">ready <InfoTip term="readiness" /></span>
              </th>
              <th className="px-1 pb-2 font-medium">
                <span className="inline-flex items-center gap-1">gate <InfoTip term="gate" /></span>
              </th>
              <th className="px-1 pb-2 font-medium">
                <span className="inline-flex items-center gap-1">stability <InfoTip term="stabilityGrade" /></span>
              </th>
              <th className="px-1 pb-2 font-medium">
                <span className="inline-flex items-center gap-1">forward <InfoTip term="forwardCheck" /></span>
              </th>
              <th className="px-1 pb-2 text-right font-medium">
                <span className="inline-flex items-center gap-1">bt ret <InfoTip term="backtestReturn" /></span>
              </th>
              <th className="px-1 pb-2 text-right font-medium">
                <span className="inline-flex items-center gap-1">win% <InfoTip term="windowWinRate" /></span>
              </th>
            </tr>
          </thead>
          <tbody>
            {arms.map((s) => {
              const rv = readinessView(s.readiness);
              const sv = survivalView(s.survival);
              const stv = stabilityView(s.stability);
              const fv = forwardView(s.forward);
              const isLive = s.readiness?.state === "incumbent";
              return (
                <tr
                  key={s.name}
                  className={`border-t border-edge/60 align-middle ${isLive ? "bg-violet/10" : ""}`}
                >
                  <td className="px-1 py-2">
                    <span className="font-display text-[13px] font-bold text-ink">{s.name}</span>
                  </td>
                  <td className="px-1 py-2">
                    <StatusPill tone={rv.tone} srText={rv.title}>
                      {rv.label}
                    </StatusPill>
                  </td>
                  <td className="px-1 py-2">
                    <span className="flex flex-col gap-0.5">
                      <StatusPill tone={sv.tone}>{sv.label}</StatusPill>
                      {sv.detail && <span className="font-mono text-[9px] text-muted">{sv.detail}</span>}
                    </span>
                  </td>
                  <td className="px-1 py-2">
                    <StatusPill tone={stv.tone}>{stv.label}</StatusPill>
                  </td>
                  <td className="px-1 py-2">
                    <StatusPill tone={fv.tone}>{fv.label}</StatusPill>
                  </td>
                  {/* SCOREBOARD — neutral tone on purpose (not an edge claim) */}
                  <td className="px-1 py-2 text-right font-mono text-sub">{btRet(s.scoreboard)}</td>
                  <td className="px-1 py-2 text-right font-mono text-sub">{winWindow(s.scoreboard)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile: one card per arm, same data, tap to select */}
      <ul className="space-y-2 md:hidden">
        {arms.map((s) => {
          const rv = readinessView(s.readiness);
          const sv = survivalView(s.survival);
          const stv = stabilityView(s.stability);
          const fv = forwardView(s.forward);
          const isLive = s.readiness?.state === "incumbent";
          return (
            <li key={s.name}>
              <div
                className={`w-full rounded-sm border-3 p-2.5 text-left shadow-brut-sm ${
                  isLive ? "border-violet/60 bg-violet/10" : "border-edge bg-transparent"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-display text-sm font-bold text-ink">{s.name}</span>
                  <StatusPill tone={rv.tone} srText={rv.title}>
                    {rv.label}
                  </StatusPill>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                  <span className="flex items-center gap-1 text-muted">
                    gate
                    <StatusPill tone={sv.tone}>{sv.label}</StatusPill>
                    {sv.detail && <span className="font-mono text-[9px] text-muted">{sv.detail}</span>}
                  </span>
                  <span className="flex items-center gap-1 text-muted">
                    stability <StatusPill tone={stv.tone}>{stv.label}</StatusPill>
                  </span>
                  <span className="flex items-center gap-1 text-muted">
                    forward <StatusPill tone={fv.tone}>{fv.label}</StatusPill>
                  </span>
                  <span className="flex items-center gap-1 text-muted">
                    bt ret <span className="font-mono text-sub">{btRet(s.scoreboard)}</span>
                    <span className="ml-1">win {winWindow(s.scoreboard)}</span>
                  </span>
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        {`Read-only validation across all arms. 🔒 LIVE = the live arm${
          liveArmName ? ` (${liveArmName})` : ""
        }, operator-controlled (live_tick.sh <arm>). Survival is the hard gate; the scoreboard is not an edge claim.`}
      </p>
      </Collapsible>
    </Card>
  );
}
