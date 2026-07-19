import { useState } from "react";
import type { ReactNode } from "react";

interface CollapsibleProps {
  title: string;
  /** localStorage key suffix — open/closed persists per band. */
  id: string;
  defaultOpen?: boolean;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}

/**
 * A band-level section header that collapses its children. Real <button aria-expanded>,
 * keyboard-operable. Defaults to OPEN and only persists an explicit user collapse, so a
 * judge never lands on a hidden panel by accident.
 */
export default function Collapsible({ title, id, defaultOpen = true, right, children, className = "" }: CollapsibleProps) {
  const key = `mc.collapse.${id}`;
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(key);
      return v === null ? defaultOpen : v === "1";
    } catch {
      return defaultOpen;
    }
  });

  const toggle = () =>
    setOpen((o) => {
      const next = !o;
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch {
        /* storage unavailable — keep in-memory only */
      }
      return next;
    });

  return (
    <section className={className}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="mb-3 flex w-full items-center gap-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/50"
      >
        <span className="font-display text-xs font-bold uppercase tracking-[0.2em] text-sub">{title}</span>
        <span className="h-px flex-1 bg-edge" />
        {right}
        <span className="font-mono text-xs text-muted" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && children}
    </section>
  );
}
