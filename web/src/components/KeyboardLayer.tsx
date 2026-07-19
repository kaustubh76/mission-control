import { useEffect, useRef, useState } from "react";

import type { UseAllocator } from "../hooks/useAllocator";
import { useTheme } from "../hooks/useTheme";
import {
  NAV_KEYS,
  OPEN_CHEATSHEET_EVENT,
  overlayOpen,
  POWER_KEYS,
  type PowerKey,
} from "../lib/cockpit";
import { flashSection, sectionId } from "../lib/sections";
import { useToast } from "./ui/Toast";

/**
 * The global keyboard layer behind the ⌘K palette: safe single-key power actions
 * (?, t, r, .) and a vim-style `g`-then-key quick-jump to any panel. One window
 * listener, bound once (reads live handlers from a ref), and it stands down whenever
 * focus is in a field, a modifier is held, or any cockpit overlay is open — so it
 * never fights the palette/cheatsheet/tour. A small leader-hint chip shows the panel
 * keys while `g` is armed.
 */
export default function KeyboardLayer({ allocator }: { allocator: UseAllocator }) {
  const { toast } = useToast();
  const { toggle: toggleTheme } = useTheme();
  const [leader, setLeader] = useState(false);

  // Latest handlers in a ref so the keydown listener binds exactly once.
  const ref = useRef({ allocator, toggleTheme, toast });
  ref.current = { allocator, toggleTheme, toast };

  useEffect(() => {
    const leaderRef = { armed: false };
    let leaderTimer: number | undefined;

    const disarm = () => {
      leaderRef.armed = false;
      window.clearTimeout(leaderTimer);
      setLeader(false);
    };

    const runPower = (pk: PowerKey) => {
      const { allocator: a, toggleTheme: tt, toast: to } = ref.current;
      switch (pk.action) {
        case "cheatsheet":
          window.dispatchEvent(new Event(OPEN_CHEATSHEET_EVENT));
          break;
        case "theme":
          tt();
          break;
        case "refresh":
          void a.refresh();
          to.info("Refreshing…", { key: "kbd-refresh", ttl: 1400 });
          break;
      }
    };

    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (overlayOpen()) return;

      // Second key of a `g` sequence → jump to a panel (or g g → top).
      if (leaderRef.armed) {
        disarm();
        if (e.key === "g") {
          e.preventDefault();
          window.scrollTo({ top: 0, behavior: "smooth" });
          return;
        }
        const nav = NAV_KEYS.find((n) => n.key === e.key.toLowerCase());
        if (nav) {
          e.preventDefault();
          flashSection(sectionId(nav.label));
        }
        return;
      }

      // Arm the `g` leader; `G` jumps to the bottom.
      if (e.key === "g") {
        e.preventDefault();
        leaderRef.armed = true;
        setLeader(true);
        leaderTimer = window.setTimeout(disarm, 1400);
        return;
      }
      if (e.key === "G") {
        e.preventDefault();
        window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
        return;
      }

      const pk = POWER_KEYS.find((p) => p.key === e.key);
      if (pk) {
        e.preventDefault();
        runPower(pk);
      }
    };

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(leaderTimer);
    };
  }, []);

  if (!leader) return null;
  return (
    <div className="fixed bottom-4 left-4 z-[55] flex max-w-[min(92vw,420px)] flex-wrap items-center gap-1.5 rounded-sm border-3 border-[color:var(--thick-line)] bg-panel2 px-3 py-2 shadow-brut-sm">
      <span className="mr-1 font-display text-[10px] font-bold uppercase tracking-wider text-muted">
        <kbd className="mc-kbd">g</kbd> jump to
      </span>
      {NAV_KEYS.map((n) => (
        <kbd key={n.key} className="mc-kbd" title={n.label}>
          {n.key}
        </kbd>
      ))}
    </div>
  );
}
