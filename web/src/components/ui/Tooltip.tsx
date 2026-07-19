import type { ReactNode } from "react";

import { GLOSSARY, type GlossaryKey } from "../../lib/glossary";

interface InfoTipProps {
  /** Glossary key — fills the title + body automatically. */
  term?: GlossaryKey;
  /** Override / standalone title (when not using a glossary term). */
  title?: string;
  /** Override / standalone body text. */
  text?: string;
  side?: "top" | "bottom";
  className?: string;
  children?: ReactNode;
}

/**
 * Tiny accessible "ⓘ" affordance. The popover shows on hover AND keyboard focus
 * (focus-within), so jargon is decodable without a mouse. No portal/lib — relies on
 * the parent card NOT clipping overflow (Card.tsx drops overflow-hidden for this).
 */
export default function InfoTip({ term, title, text, side = "top", className = "", children }: InfoTipProps) {
  const entry = term ? GLOSSARY[term] : undefined;
  const head = title ?? entry?.term ?? "";
  const body = text ?? entry?.plain ?? "";
  if (!body) return null;

  return (
    <span className={`group relative inline-flex align-middle ${className}`}>
      <button
        type="button"
        aria-label={`What is ${head}?`}
        className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-muted/60 font-display text-[9px] font-bold leading-none text-muted transition-colors hover:border-brand hover:text-brand focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
      >
        {children ?? "i"}
      </button>
      <span
        role="tooltip"
        className={`pointer-events-none absolute left-1/2 z-50 w-56 -translate-x-1/2 rounded-sm border-3 border-[color:var(--thick-line)] bg-panel px-2.5 py-2 text-left opacity-0 shadow-brut-sm transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100 ${
          side === "top" ? "bottom-full mb-2" : "top-full mt-2"
        }`}
      >
        <span className="block font-display text-[11px] font-bold uppercase tracking-wide text-ink">{head}</span>
        <span className="mt-0.5 block text-[11px] leading-snug text-sub">{body}</span>
      </span>
    </span>
  );
}
