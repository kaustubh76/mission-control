import { useEffect, useRef } from "react";

import type { Snapshot } from "../api/types";
import { fmtSignedPct } from "../lib/format";
import { useToast } from "../components/ui/Toast";
import type { UseAllocator } from "./useAllocator";

const AWAY_MS = 30_000; // ignore quick tab flicks — only summarize a real absence

interface Snap {
  t: number;
  nav: number | null;
  swaps: number;
  regime: string;
  killed: boolean;
}

function snap(d: Snapshot): Snap {
  return {
    t: Date.now(),
    nav: d.nav.current_nav,
    swaps: d.state.cumulative_swaps,
    regime: d.regime.fear_greed_label,
    killed: d.health.kill_switch_engaged,
  };
}

const awayLabel = (s: number) =>
  s < 90 ? `${Math.round(s)}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;

/** Compose a one-line "what changed" summary, including only the parts that moved. */
function buildDigest(base: Snap, cur: Snapshot): string | null {
  const parts: string[] = [];
  if (base.nav != null && cur.nav.current_nav != null && base.nav > 0) {
    const pct = (cur.nav.current_nav - base.nav) / base.nav;
    if (Math.abs(pct) >= 0.0005) parts.push(`NAV ${fmtSignedPct(pct)}`);
  }
  const dSwaps = cur.state.cumulative_swaps - base.swaps;
  if (dSwaps > 0) parts.push(`${dSwaps} rebalance${dSwaps === 1 ? "" : "s"}`);
  const regime = cur.regime.fear_greed_label;
  if (regime && regime !== base.regime) parts.push(`regime → ${regime}`);
  if (cur.health.kill_switch_engaged !== base.killed)
    parts.push(cur.health.kill_switch_engaged ? "kill switch ENGAGED" : "kill switch released");
  if (parts.length === 0) return null;
  return `Away ${awayLabel((Date.now() - base.t) / 1000)} · ${parts.join(" · ")}`;
}

/**
 * "While you were away" digest. Snapshots key metrics when the tab is hidden (reusing
 * the visibilitychange signal) and, on return after ≥30s, waits for the next fresh poll
 * then toasts a one-line summary of what changed. Gated on `live` at both ends so the
 * frozen demo snapshot never produces a bogus digest.
 */
export function useAwayDigest(allocator: UseAllocator) {
  const { toast } = useToast();
  const { data, lastUpdated, live } = allocator;

  const dataRef = useRef(data);
  dataRef.current = data;
  const liveRef = useRef(live);
  liveRef.current = live;
  const baseline = useRef<Snap | null>(null);
  const pending = useRef<Snap | null>(null);

  useEffect(() => {
    const onVis = () => {
      const d = dataRef.current;
      if (document.hidden) {
        baseline.current = d && liveRef.current ? snap(d) : null;
      } else {
        if (baseline.current && Date.now() - baseline.current.t >= AWAY_MS) pending.current = baseline.current;
        baseline.current = null;
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  // Fires when a fresh poll lands after returning — emit against the captured baseline.
  useEffect(() => {
    const base = pending.current;
    const d = dataRef.current;
    if (!base || !d || !liveRef.current) return;
    pending.current = null;
    const msg = buildDigest(base, d);
    if (msg) toast.info(msg, { key: "away-digest", title: "Welcome back", ttl: 7000 });
  }, [lastUpdated, toast]);
}
