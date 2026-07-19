import type { Evaluation } from "../api/types";
import Card from "./ui/Card";
import StatusPill, { type Tone } from "./ui/StatusPill";

const UP = "#16c784";
const DOWN = "#ea3943";
const AMBER = "#f0b90b";
const NEU = "#8a8f9c";

const fmtScore = (v: number | null | undefined): string => (v == null ? "—" : v.toFixed(3));
const stabilityClr = (g: string | null): string =>
  g === "ROBUST" ? UP : g === "FRAGILE" ? AMBER : g === "UNSTABLE" ? DOWN : NEU;

/**
 * Forward-gated strategy auto-selector — the honest "switching engine".
 * Ranks every DQ-safe arm by risk-adjusted FORWARD score and recommends the best, with anti-chasing
 * hysteresis (a challenger must be forward-eligible + ROBUST AND beat the incumbent by a margin for N
 * consecutive evals). SIM is auto-driven; LIVE is RECOMMEND-ONLY — the operator applies via the shown
 * command. Expect "STAY" until an arm earns it (it will NOT chase a recent spike).
 */
export default function AutoSelectorCard({ sel }: { sel: Evaluation | null | undefined }) {
  if (!sel) {
    return (
      <Card label="Auto-selector" accent="#7C5CFF">
        <div className="flex h-20 items-center justify-center text-xs text-muted">
          evaluator hasn't run yet
        </div>
      </Card>
    );
  }
  const isSwitch = sel.action === "SWITCH";
  const tone: Tone = isSwitch ? "up" : "neutral";
  const h = sel.hysteresis;

  return (
    <Card
      label="Auto-selector"
      accent="#7C5CFF"
      right={
        <StatusPill tone={tone} dot srText={`auto-selector ${sel.action ?? "n/a"}`}>
          {sel.action ?? "—"}
        </StatusPill>
      }
    >
      <div className="space-y-2 text-xs">
        <div className="flex items-baseline justify-between">
          <span className="text-muted">recommended live arm</span>
          <span className="font-mono font-semibold" style={{ color: isSwitch ? UP : undefined }}>
            {sel.recommended ?? "—"}
          </span>
        </div>
        <div className="flex items-baseline justify-between">
          <span className="text-muted">current live arm</span>
          <span className="font-mono">{sel.current ?? "—"}</span>
        </div>
        {h && (
          <div className="text-[11px] text-muted">
            hysteresis: champion held {h.consecutive ?? 0}/{h.sustain ?? "—"} evals · margin{" "}
            {h.margin ?? "—"}
          </div>
        )}
        {sel.reason && <div className="text-[11px] leading-snug text-muted">{sel.reason}</div>}

        {/* Ranked candidates (DQ-safe first) */}
        <div className="mt-1 space-y-1">
          {sel.scores.map((s) => (
            <div key={s.arm} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1 font-mono">
                {s.arm === sel.recommended && <span style={{ color: UP }}>★</span>}
                {s.arm}
              </span>
              <span className="flex items-center gap-2">
                <span className="font-mono tabular-nums">{fmtScore(s.score)}</span>
                <span title="DQ-safe" style={{ color: s.dq_safe ? UP : DOWN }}>
                  {s.dq_safe ? "DQ✓" : "DQ✗"}
                </span>
                <span title="forward-eligible" style={{ color: s.forward_eligible ? UP : NEU }}>
                  {s.forward_eligible ? "FWD✓" : "FWD·"}
                </span>
                <span title="stability" style={{ color: stabilityClr(s.stability) }}>
                  {s.stability ?? "—"}
                </span>
              </span>
            </div>
          ))}
        </div>

        {isSwitch && sel.apply_cmd ? (
          <div className="mt-1 rounded bg-black/20 px-2 py-1 font-mono text-[11px]">
            apply: {sel.apply_cmd}
          </div>
        ) : (
          <div className="mt-1 text-[11px] text-muted">
            live is recommend-only{sel.auto_apply_live ? " (auto-apply ON)" : ""} — applied on operator
            sign-off
          </div>
        )}
      </div>
    </Card>
  );
}
