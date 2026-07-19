import { useCallback, useEffect, useRef, useState } from "react";

import {
  getSnapshot,
  lastFromStatic,
  postActiveTokens,
  postKill,
  postStrategySelect,
} from "../api/client";
import type {
  KillResult,
  Snapshot,
  StrategySelectResult,
  TokensResult,
} from "../api/types";

export interface UseAllocator {
  data: Snapshot | null;
  error: string | null;
  lastUpdated: number | null;
  stale: boolean;
  busy: boolean;
  live: boolean; // false when served from the committed static snapshot (no live API)
  refresh: () => Promise<void>;
  toggleKill: (engage: boolean) => Promise<KillResult>;
  setActiveTokens: (active: string[]) => Promise<TokensResult>;
  setStrategy: (strategy: string) => Promise<StrategySelectResult>;
}

/**
 * Polls /api/snapshot every `intervalMs` (default 4s). Pauses while the tab is
 * hidden, refreshes on focus, and keeps the last good data on a transient error
 * (so the dashboard never blanks). Exposes the two guarded controls, each of
 * which re-fetches immediately so the UI reflects the new state without waiting
 * for the next poll.
 */
export function useAllocator(intervalMs = 4000): UseAllocator {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const s = await getSnapshot();
      setData(s);
      setError(null);
      setLastUpdated(Date.now());
      setLive(!lastFromStatic);
    } catch (e) {
      setError(e instanceof Error ? e.message : "fetch failed");
      // keep previous data — do not blank the dashboard on a blip
    }
  }, []);

  useEffect(() => {
    refresh();
    const onTick = () => {
      if (!document.hidden) void refresh();
    };
    timer.current = window.setInterval(onTick, intervalMs);
    const onVisible = () => {
      if (!document.hidden) void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer.current);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh, intervalMs]);

  const toggleKill = useCallback(
    async (engage: boolean) => {
      setBusy(true);
      try {
        const r = await postKill(engage);
        await refresh();
        return r;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const setActiveTokens = useCallback(
    async (active: string[]) => {
      setBusy(true);
      try {
        const r = await postActiveTokens(active);
        await refresh();
        return r;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const setStrategy = useCallback(
    async (strategy: string) => {
      setBusy(true);
      try {
        const r = await postStrategySelect(strategy);
        await refresh();
        return r;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const stale = lastUpdated ? Date.now() - lastUpdated > intervalMs * 2.5 : false;

  return {
    data, error, lastUpdated, stale, busy, live,
    refresh, toggleKill, setActiveTokens, setStrategy,
  };
}
