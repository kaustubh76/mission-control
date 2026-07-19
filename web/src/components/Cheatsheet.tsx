import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { NAV_KEYS, OPEN_CHEATSHEET_EVENT, POWER_KEYS } from "../lib/cockpit";
import { sectionId } from "../lib/sections";

function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="mc-kbd">{children}</kbd>;
}

function Row({ keys, label }: { keys: ReactNode; label: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-[13px] text-sub">{label}</span>
      <span className="flex shrink-0 items-center gap-1">{keys}</span>
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 font-display text-[9px] font-bold uppercase tracking-[0.18em] text-muted">{title}</div>
      <div className="divide-y divide-edge/40">{children}</div>
    </div>
  );
}

/**
 * The `?` keyboard-shortcuts cheatsheet. Opens on the OPEN_CHEATSHEET_EVENT bus
 * (dispatched by `?`, the ⌘K palette, and the header chip). The Navigation group is
 * built from NAV_KEYS filtered to the panels actually in the DOM, so it never lists a
 * jump that won't resolve. Mirrors the palette's modal styling; reduced-motion safe.
 */
export default function Cheatsheet() {
  const reduce = useReducedMotion();
  const [open, setOpen] = useState(false);
  const [navKeys, setNavKeys] = useState(NAV_KEYS);
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const onOpen = () => {
      restoreRef.current = document.activeElement as HTMLElement | null;
      setNavKeys(NAV_KEYS.filter((n) => document.getElementById(sectionId(n.label))));
      setOpen(true);
    };
    window.addEventListener(OPEN_CHEATSHEET_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_CHEATSHEET_EVENT, onOpen);
  }, []);

  const close = () => {
    setOpen(false);
    restoreRef.current?.focus?.();
  };

  useEffect(() => {
    if (open) setTimeout(() => panelRef.current?.focus(), 0);
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="mc-overlay fixed inset-0 z-[70] flex items-start justify-center bg-black/50 p-4 pt-[10vh] backdrop-blur-sm"
          initial={reduce ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onMouseDown={close}
        >
          <motion.div
            ref={panelRef}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-label="Keyboard shortcuts"
            className="glow-card w-full max-w-[560px] overflow-hidden p-0 outline-none"
            initial={reduce ? false : { opacity: 0, y: -8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            onMouseDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                close();
              }
            }}
          >
            <header className="flex items-center justify-between border-b border-edge px-4 py-3">
              <span className="font-display text-sm font-bold text-ink">Keyboard shortcuts</span>
              <button
                onClick={close}
                aria-label="close"
                className="rounded-sm px-1 text-muted transition hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
              >
                ✕
              </button>
            </header>

            <div className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2">
              <Group title="Navigation">
                <Row keys={<><Kbd>g</Kbd><span className="text-muted">g</span></>} label="Top of page" />
                <Row keys={<><Kbd>g</Kbd><Kbd>G</Kbd></>} label="Bottom of page" />
                {navKeys.map((n) => (
                  <Row key={n.key} keys={<><Kbd>g</Kbd><Kbd>{n.key}</Kbd></>} label={n.label} />
                ))}
              </Group>

              <div className="space-y-4">
                <Group title="Actions">
                  {POWER_KEYS.map((p) => (
                    <Row key={p.key} keys={<Kbd>{p.key}</Kbd>} label={p.label} />
                  ))}
                </Group>
                <Group title="General">
                  <Row keys={<Kbd>⌘K</Kbd>} label="Command palette" />
                  <Row keys={<><Kbd>↑</Kbd><Kbd>↓</Kbd></>} label="Move selection (palette/tour)" />
                  <Row keys={<Kbd>⏎</Kbd>} label="Run selected command" />
                  <Row keys={<Kbd>esc</Kbd>} label="Close any overlay" />
                </Group>
              </div>
            </div>

            <div className="border-t border-edge px-4 py-2 font-mono text-[10px] text-muted">
              tip: press <span className="text-sub">g</span> then a panel key to jump anywhere
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
