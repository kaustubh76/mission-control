import { useEffect, useRef } from "react";

import type { UseAllocator } from "./useAllocator";
import { useToast } from "../components/ui/Toast";

/**
 * Watches the live agent state and pops a toast when a functional/risk condition
 * changes. Edge-detection only: each `prev` ref seeds to the HEALTHY baseline, so a
 * state that is already bad on first load surfaces exactly once (healthy→bad), and
 * dedup-by-key keeps it from repeating on every 4s poll. Steady state is silent.
 */
export function useFunctionalAlerts(allocator: UseAllocator): void {
  const { toast } = useToast();
  const { data, error, live, stale } = allocator;

  // Seed all baselines to "healthy" so an already-bad first load alerts once.
  const prev = useRef({
    error: false,
    live: true,
    stale: false,
    kill: false,
    halted: false,
    gasLow: false,
    ddCaution: false,
  });

  useEffect(() => {
    // Wait for the first fetch to resolve before evaluating transitions. Otherwise the
    // allocator's initial live=false (pre-first-poll) reads as a healthy→demo→restored
    // flip and pops a spurious "Live data restored" on every fresh load.
    if (!data && error == null) return;

    const p = prev.current;

    // ── Connectivity ──
    const hasError = error != null;
    if (hasError !== p.error) {
      if (hasError) toast.error("Lost connection to the agent — retrying…", { key: "conn", title: "Offline" });
      else toast.success("Reconnected to the agent.", { key: "conn" });
      p.error = hasError;
    }

    // ── Live API vs cached demo snapshot ──
    if (live !== p.live) {
      if (!live) toast.warn("Live API unreachable — showing the last cached snapshot.", { key: "live", title: "Demo data" });
      else toast.success("Live data restored.", { key: "live" });
      p.live = live;
    }

    // ── Stale data (only meaningful while we believe we're live) ──
    const isStale = stale && live;
    if (isStale !== p.stale) {
      if (isStale) toast.warn("Data looks stale — the agent may have paused.", { key: "stale", title: "Stale" });
      p.stale = isStale;
    }

    if (!data) return;

    // ── Kill switch ──
    const kill = !!data.health?.kill_switch_engaged;
    if (kill !== p.kill) {
      if (kill) toast.error("Kill switch ENGAGED — the agent will not trade.", { key: "kill", title: "Kill switch" });
      else toast.success("Kill switch released.", { key: "kill" });
      p.kill = kill;
    }

    // ── Drawdown halt ──
    const halted = !!data.state?.halted;
    if (halted !== p.halted) {
      if (halted) toast.error("Drawdown HALT — the book is flat and paused.", { key: "halt", title: "Halted" });
      p.halted = halted;
    }

    // ── Drawdown crossed the team cap (caution band, below the DQ line) ──
    const dd = data.nav?.drawdown?.current ?? 0;
    const team = data.nav?.caps?.team ?? Infinity;
    const dq = data.nav?.caps?.dq ?? Infinity;
    const caution = dd >= team && dd < dq;
    if (caution !== p.ddCaution) {
      if (caution) {
        toast.warn(`Drawdown ${(dd * 100).toFixed(1)}% — past the ${(team * 100).toFixed(0)}% team cap.`, {
          key: "dd",
          title: "Drawdown",
        });
      }
      p.ddCaution = caution;
    }

    // ── Trade-gas buffer ──
    const gasLow = !!data.wallet?.gas_low;
    if (gasLow !== p.gasLow) {
      if (gasLow) toast.warn("Trade-gas buffer low — top up BNB before live trading.", { key: "gas", title: "Low gas" });
      p.gasLow = gasLow;
    }
  }, [data, error, live, stale, toast]);
}
