import { useReducedMotion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";

import { START_TOUR_EVENT, TOUR_STEPS } from "../lib/cockpit";
import { sectionId } from "../lib/sections";

interface Step {
  label: string;
  narration: string;
  id: string;
}

const PAD = 6; // breathing room around the highlighted panel

/**
 * Judge-facing presenter mode. Launched from the ⌘K palette (START_TOUR_EVENT), it
 * dims the page and spotlights each panel in turn with a one-line narration. Panels are
 * auto-discovered from `[data-section-label]` (same source as the palette), so the tour
 * never references a panel that isn't on screen. The dim is a single box-shadow-spread
 * over the target rect — cheap, and it glides between steps. Arrow keys / Esc drive it;
 * reduced-motion turns off the glide. Root carries `.mc-overlay` so the keyboard layer
 * stands down while it's open.
 */
export default function Tour() {
  const reduce = useReducedMotion();
  const [steps, setSteps] = useState<Step[]>([]);
  const [idx, setIdx] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const open = steps.length > 0;
  const nextRef = useRef<HTMLButtonElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const onStart = () => {
      const built = TOUR_STEPS.map((s) => ({ ...s, id: sectionId(s.label) })).filter((s) =>
        document.getElementById(s.id),
      );
      if (!built.length) return;
      restoreRef.current = document.activeElement as HTMLElement | null;
      setIdx(0);
      setSteps(built);
    };
    window.addEventListener(START_TOUR_EVENT, onStart);
    return () => window.removeEventListener(START_TOUR_EVENT, onStart);
  }, []);

  const close = useCallback(() => {
    setSteps([]);
    setRect(null);
    restoreRef.current?.focus?.();
  }, []);

  const nextOrDone = useCallback(() => {
    setIdx((i) => {
      if (i >= steps.length - 1) {
        close();
        return i;
      }
      return i + 1;
    });
  }, [steps.length, close]);

  const prev = useCallback(() => setIdx((i) => Math.max(0, i - 1)), []);

  // Scroll the current target into view and keep the spotlight glued to it.
  useEffect(() => {
    if (!open) return;
    const step = steps[idx];
    const el = step && document.getElementById(step.id);
    if (!el) return;
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    const measure = () => setRect(el.getBoundingClientRect());
    measure();
    let raf = 0;
    const onMove = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    };
    window.addEventListener("scroll", onMove, { passive: true });
    window.addEventListener("resize", onMove);
    const settle = window.setTimeout(measure, 420);
    return () => {
      window.removeEventListener("scroll", onMove);
      window.removeEventListener("resize", onMove);
      cancelAnimationFrame(raf);
      window.clearTimeout(settle);
    };
  }, [open, idx, steps, reduce]);

  // Keyboard nav while open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      } else if (e.key === "ArrowRight" || e.key === " ") {
        e.preventDefault();
        nextOrDone();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        prev();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, nextOrDone, prev, close]);

  useEffect(() => {
    if (open) setTimeout(() => nextRef.current?.focus(), 0);
  }, [open, idx]);

  if (!open) return null;
  const step = steps[idx];
  const last = idx === steps.length - 1;

  return (
    <div className="mc-overlay fixed inset-0 z-[75]" onClick={nextOrDone} aria-hidden={false}>
      {rect && (
        <div
          className="pointer-events-none fixed rounded-sm"
          style={{
            top: rect.top - PAD,
            left: rect.left - PAD,
            width: rect.width + PAD * 2,
            height: rect.height + PAD * 2,
            boxShadow: "0 0 0 9999px rgba(0,0,0,0.62)",
            outline: "2px solid var(--thick-line)",
            outlineOffset: "0px",
            transition: reduce ? "none" : "top 0.25s ease, left 0.25s ease, width 0.25s ease, height 0.25s ease",
          }}
        />
      )}

      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Tour: ${step.label}`}
        onClick={(e) => e.stopPropagation()}
        className="glow-card fixed bottom-6 left-1/2 w-[min(92vw,460px)] -translate-x-1/2 p-4"
      >
        <div className="mb-1 flex items-center justify-between gap-3">
          <span className="font-display text-[9px] font-bold uppercase tracking-[0.18em] text-muted">
            Step {idx + 1} / {steps.length}
          </span>
          <span className="card-label">{step.label}</span>
        </div>
        <p className="text-[13px] leading-snug text-sub">{step.narration}</p>

        <div className="mt-3 flex items-center justify-between gap-3">
          <button
            onClick={close}
            className="font-mono text-[11px] text-muted transition hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
          >
            esc · skip
          </button>
          <div className="flex items-center gap-1.5">
            {steps.map((_, i) => (
              <span
                key={i}
                className="h-1.5 w-1.5 rounded-full transition-colors"
                style={{ background: i === idx ? "var(--thick-line)" : "rgb(var(--c-edge))" }}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={prev}
              disabled={idx === 0}
              className="rounded-sm border border-edge px-2 py-1 font-display text-[11px] font-bold text-sub transition hover:border-muted disabled:opacity-30 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
            >
              ← Prev
            </button>
            <button
              ref={nextRef}
              onClick={nextOrDone}
              className="rounded-sm border-3 border-cool/50 bg-cool/10 px-3 py-1 font-display text-[11px] font-bold text-cyan shadow-brut-sm transition hover:bg-cool/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
            >
              {last ? "Done" : "Next →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
