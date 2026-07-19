import { useEffect, useState } from "react";

/** Re-renders on an interval so relative-time labels ("updated 3s ago") keep ticking.
 * Returns the current epoch-ms. Default 1s cadence. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
