import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { UseAllocator } from "../hooks/useAllocator";
import { useTheme } from "../hooks/useTheme";
import { OPEN_CHEATSHEET_EVENT, START_TOUR_EVENT } from "../lib/cockpit";
import { flashSection } from "../lib/sections";
import { useToast } from "./ui/Toast";

/** Fire from anywhere (e.g. a header chip) to open the palette without prop-drilling. */
export const OPEN_PALETTE_EVENT = "mc:command-palette";

interface Command {
  id: string;
  label: string;
  group: "Go to" | "Actions";
  hint?: string;
  keywords?: string;
  run: () => void;
}

/**
 * ⌘K / Ctrl-K command palette: fuzzy-jump to any panel (discovered live from the
 * DOM) and fire the guarded controls. Self-contained — listens for the keys and an
 * OPEN_PALETTE_EVENT, so a header chip can open it without shared state.
 */
export default function CommandPalette({ allocator }: { allocator: UseAllocator }) {
  const reduce = useReducedMotion();
  const { toast } = useToast();
  const { theme, toggle: toggleTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setSel(0);
  }, []);

  // Global open/close: ⌘K / Ctrl-K toggles; the header chip dispatches an event.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(OPEN_PALETTE_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(OPEN_PALETTE_EVENT, onOpen);
    };
  }, []);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const killed = allocator.data?.health.kill_switch_engaged ?? false;
  const live = allocator.live;

  // Build commands fresh each open: nav targets discovered from the live DOM +
  // the guarded actions. Recomputed when the palette opens or state changes.
  const commands = useMemo<Command[]>(() => {
    if (!open) return [];
    const navEls = Array.from(document.querySelectorAll<HTMLElement>("[data-section-label]"));
    const nav: Command[] = navEls.map((el) => {
      const label = el.dataset.sectionLabel || el.id;
      return {
        id: el.id,
        label,
        group: "Go to",
        hint: "panel",
        run: () => {
          flashSection(el.id);
          close();
        },
      };
    });

    const act = (label: string, run: () => void, keywords?: string): Command => ({
      id: `act-${label}`,
      label,
      group: "Actions",
      keywords,
      run: () => {
        run();
        close();
      },
    });

    const guarded = (fn: () => Promise<unknown>) => () => {
      if (!live) {
        toast.warn("Controls need the live API — connect the backend.", { key: "ctrl-offline", title: "Offline" });
        return;
      }
      void fn();
    };

    const actions: Command[] = [
      act(killed ? "Release kill switch" : "Engage kill switch", guarded(() => allocator.toggleKill(!killed)), "halt stop emergency"),
      act("Refresh data now", () => void allocator.refresh(), "reload poll"),
      act(`Switch to ${theme === "dark" ? "light" : "dark"} theme`, toggleTheme, "dark light appearance"),
      act("Take the 60-second tour", () => window.dispatchEvent(new Event(START_TOUR_EVENT)), "guide walkthrough presenter demo judge"),
      act("Show keyboard shortcuts", () => window.dispatchEvent(new Event(OPEN_CHEATSHEET_EVENT)), "keys cheatsheet help hotkeys"),
    ];
    return [...nav, ...actions];
  }, [open, live, killed, theme, allocator, toggleTheme, toast, close]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => (c.label + " " + (c.keywords ?? "")).toLowerCase().includes(q));
  }, [commands, query]);

  // keep selection in range as the filter narrows
  useEffect(() => {
    setSel((s) => Math.min(s, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  const onListKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      filtered[sel]?.run();
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  };

  let lastGroup = "";

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="mc-overlay fixed inset-0 z-[60] flex items-start justify-center bg-black/50 p-4 pt-[12vh] backdrop-blur-sm"
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onMouseDown={close}
          role="dialog"
          aria-modal="true"
          aria-label="Command palette"
        >
          <motion.div
            className="glow-card w-full max-w-[560px] overflow-hidden p-0"
            initial={reduce ? false : { opacity: 0, y: -8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            onMouseDown={(e) => e.stopPropagation()}
            onKeyDown={onListKey}
          >
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSel(0);
              }}
              placeholder="Jump to a panel or run an action…"
              className="w-full border-b border-edge bg-transparent px-4 py-3 font-display text-sm text-ink outline-none transition-colors focus:border-brand/70 placeholder:text-muted"
            />
            <ul className="max-h-[52vh] overflow-y-auto py-1">
              {filtered.length === 0 && (
                <li className="px-4 py-6 text-center text-xs text-muted">no matches</li>
              )}
              {filtered.map((c, i) => {
                const showGroup = c.group !== lastGroup;
                lastGroup = c.group;
                return (
                  <li key={c.id}>
                    {showGroup && (
                      <div className="px-4 pb-1 pt-2 font-display text-[9px] font-bold uppercase tracking-[0.18em] text-muted">
                        {c.group}
                      </div>
                    )}
                    <button
                      onMouseEnter={() => setSel(i)}
                      onClick={() => c.run()}
                      className={`flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-[13px] transition-colors duration-150 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/50 ${
                        i === sel ? "bg-cool/15 text-ink" : "text-sub hover:bg-panel2/60"
                      }`}
                    >
                      <span className="truncate">{c.label}</span>
                      {c.hint && <span className="shrink-0 font-mono text-[10px] text-muted">{c.hint}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
            <div className="flex items-center gap-3 border-t border-edge px-4 py-2 font-mono text-[10px] text-muted">
              <span>↑↓ move</span>
              <span>⏎ run</span>
              <span>esc close</span>
              <span className="ml-auto">⌘K</span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
