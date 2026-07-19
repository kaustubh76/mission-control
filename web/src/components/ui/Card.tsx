import { motion, useReducedMotion } from "framer-motion";
import { useState } from "react";
import type { ReactNode } from "react";

type Tier = "hero" | "supporting" | "detail";

interface CardProps {
  label?: ReactNode;
  className?: string;
  accent?: string; // optional left-edge accent colour (also drives the hero hard-shadow)
  children: ReactNode;
  right?: ReactNode; // optional header-right slot
  tier?: Tier; // "hero" = bigger + bold coloured shadow; default "supporting"
  collapsible?: boolean; // detail tier — header toggles the body
  defaultOpen?: boolean;
  id?: string; // localStorage key for collapse persistence
}

/** The signature Mission Control panel: thick border + hard offset shadow, tier-aware. */
export default function Card({
  label,
  className = "",
  accent,
  children,
  right,
  tier = "supporting",
  collapsible = false,
  defaultOpen = true,
  id,
}: CardProps) {
  const reduce = useReducedMotion();
  const key = id ? `mc.card.${id}` : null;
  const [open, setOpen] = useState<boolean>(() => {
    if (!collapsible) return true;
    if (!key) return defaultOpen;
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
      if (key) {
        try {
          localStorage.setItem(key, next ? "1" : "0");
        } catch {
          /* ignore */
        }
      }
      return next;
    });

  const pad = tier === "hero" ? "p-5 md:p-6" : "p-4";
  // Hero cards get a bold COLOURED hard shadow in their accent, layered over the soft
  // ambient depth shadow; the rest use the neutral token shadow from .glow-card.
  const heroShadow =
    tier === "hero" && accent ? { boxShadow: `6px 6px 0 0 ${accent}, var(--card-ambient)` } : undefined;

  return (
    <motion.section
      initial={reduce ? false : { opacity: 0, y: 10 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-12% 0px" }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      style={heroShadow}
      className={`glow-card relative ${pad} ${className}`}
    >
      {accent && <span className="absolute inset-y-0 left-0 w-[6px]" style={{ background: accent }} />}
      {(label || right || collapsible) && (
        <header className="mb-3 flex flex-col items-start justify-between gap-2 sm:flex-row sm:items-center sm:gap-3">
          {collapsible ? (
            <button
              type="button"
              onClick={toggle}
              aria-expanded={open}
              className="flex items-center gap-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/50"
            >
              <span className="font-mono text-xs text-muted" aria-hidden>
                {open ? "▾" : "▸"}
              </span>
              {label && <span className="card-label">{label}</span>}
            </button>
          ) : (
            label && <span className="card-label">{label}</span>
          )}
          {right}
        </header>
      )}
      {open && children}
    </motion.section>
  );
}
