import { useState } from "react";

import type { UseAllocator } from "../hooks/useAllocator";
import { tokenColor } from "../lib/format";
import Card from "./ui/Card";
import StatusPill from "./ui/StatusPill";
import { useToast } from "./ui/Toast";
import InfoTip from "./ui/Tooltip";

/**
 * The multi-token control: toggle which of the 8 contest tokens the agent may
 * rank and buy. Instant per-toggle POST (declarative full-list, idempotent) with
 * optimistic UI; the server enforces min-2 and validates symbols. A deselected
 * token the book still holds shows "held — sells next tick" until the next
 * rebalance naturally exits it.
 *
 * Body-only (no Card chrome) so it can be folded into the merged Token Universe · Rotation panel.
 */
export function TokenToggleControls({ allocator }: { allocator: UseAllocator }) {
  const { data, busy, live, setActiveTokens } = allocator;
  const { toast } = useToast();
  const [msg, setMsg] = useState<string | null>(null);
  const [optimistic, setOptimistic] = useState<string[] | null>(null);

  const strategy = data?.strategy;
  const universe = strategy?.tokens ?? [];
  const serverActive = strategy?.active?.length ? strategy.active : universe;
  const active = optimistic ?? serverActive;
  const activeSet = new Set(active);
  const balances = data?.state.balances ?? {};
  const minActive = Math.max(2, strategy?.params.top_k ?? 2);

  async function onToggle(tok: string) {
    setMsg(null);
    const next = activeSet.has(tok) ? active.filter((t) => t !== tok) : [...active, tok];
    if (next.length < minActive) {
      const m = `minimum ${minActive} tokens — top-${strategy?.params.top_k ?? 2} needs candidates`;
      setMsg(m);
      toast.warn(m, { key: "tokens", title: "Token universe" });
      return;
    }
    setOptimistic(next);
    try {
      const r = await setActiveTokens(next);
      setMsg(r.message);
      if (r.ok) toast.success(r.message, { key: "tokens", title: "Token universe" });
      else toast.error(r.message, { key: "tokens", title: "Token universe" });
    } catch {
      const m = "controls need the live API — connect the Render backend";
      setMsg(m);
      toast.error(m, { key: "tokens", title: "Token universe" });
    } finally {
      // Accepted or rejected, setActiveTokens already refresh()ed the snapshot —
      // the server's strategy.active is now authoritative. Never leave optimistic
      // set, or the chips would freeze against all future polls.
      setOptimistic(null);
    }
  }

  if (universe.length === 0) {
    return <div className="text-[12px] text-muted">strategy unavailable</div>;
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted">
          active universe <InfoTip term="activeTokens" />
        </span>
        <StatusPill
          tone={active.length < universe.length ? "brand" : "neutral"}
          srText={`${active.length} of ${universe.length} tokens active`}
        >
          {active.length}/{universe.length} ACTIVE
        </StatusPill>
      </div>

      {!live && (
        <div className="mb-3 rounded-sm border border-amber/40 bg-amber/10 px-2 py-1 text-[11px] text-amber">
          demo snapshot — connect the live API to enable controls
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {universe.map((tok) => {
          const on = activeSet.has(tok);
          const held = (balances[tok] ?? 0) > 0;
          const color = tokenColor(tok);
          return (
            <button
              key={tok}
              onClick={() => onToggle(tok)}
              disabled={busy || !live}
              aria-pressed={on}
              aria-label={`${tok} ${on ? "active — click to disable" : "disabled — click to enable"}`}
              className={`group flex items-center gap-2 rounded-sm border-3 px-3 py-1.5 font-display text-sm font-bold shadow-brut-sm transition focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 disabled:cursor-not-allowed disabled:opacity-40 ${
                on ? "hover:brightness-110" : "opacity-60 hover:opacity-90"
              }`}
              style={
                on
                  ? { borderColor: `${color}99`, background: `${color}1a`, color }
                  : { borderColor: "#3a3d46", background: "transparent", color: "#8a8f9c" }
              }
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: on ? color : "#3a3d46" }}
              />
              <span className={on ? "" : "line-through"}>{tok}</span>
              {!on && held && (
                <span className="rounded-sm border border-amber/50 px-1 py-px text-[9px] font-normal normal-case tracking-wide text-amber">
                  held — sells next tick
                </span>
              )}
            </button>
          );
        })}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-muted">
        {msg ??
          "Deselected tokens are excluded from the momentum ranking; any still held are sold on the next rebalance tick. Positions below the broker's dust threshold may linger."}
      </p>
    </div>
  );
}

export default function TokenTogglePanel({ allocator }: { allocator: UseAllocator }) {
  return (
    <Card label="Token Universe" accent="#f0b90b">
      <TokenToggleControls allocator={allocator} />
    </Card>
  );
}
