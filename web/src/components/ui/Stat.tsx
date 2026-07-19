import type { ReactNode } from "react";

import type { GlossaryKey } from "../../lib/glossary";
import InfoTip from "./Tooltip";

const DIR_COLOR = { up: "#16c784", down: "#ea3943", flat: "#8a8f9c" } as const;
const DIR_ARROW = { up: "▲", down: "▼", flat: "→" } as const;
const DIR_WORD = { up: "up", down: "down", flat: "flat" } as const;

const SIZE: Record<"hero" | "md" | "sm", string> = {
  hero: "text-3xl sm:text-4xl md:text-5xl",
  md: "text-2xl",
  sm: "text-xl",
};

interface StatProps {
  /** Uppercase eyebrow label. */
  label: string;
  /** The precise value (number / formatted string / node). */
  value: ReactNode;
  /** Plain-English headline shown beneath the value. */
  plain?: string;
  /** Signed change, colour + arrow + sr-only direction word. */
  delta?: { text: string; dir: "up" | "down" | "flat" };
  /** Glossary key → an InfoTip next to the label. */
  term?: GlossaryKey;
  size?: "hero" | "md" | "sm";
  /** Value colour (defaults to inherit/slate). */
  color?: string;
  /** Add a colour-matched glow to the value (reserve for the few biggest live numbers). */
  glow?: boolean;
  className?: string;
}

/**
 * The both-audiences primitive: precise value for traders + a plain-English headline
 * for everyone else, with the jargon decodable inline via InfoTip. Status is never
 * conveyed by colour alone — the delta arrow carries a visually-hidden direction word.
 */
export default function Stat({ label, value, plain, delta, term, size = "md", color, glow = false, className = "" }: StatProps) {
  return (
    <div className={className}>
      <div className="flex items-center gap-1.5">
        <span className="card-label">{label}</span>
        {term && <InfoTip term={term} side="bottom" />}
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span
          className={`font-display font-bold leading-none tabular-nums ${SIZE[size]} ${glow ? "metric-glow" : ""}`}
          style={color ? { color } : undefined}
        >
          {value}
        </span>
        {delta && (
          <span className="inline-flex items-center gap-1 text-sm font-bold" style={{ color: DIR_COLOR[delta.dir] }}>
            <span aria-hidden>{DIR_ARROW[delta.dir]}</span>
            {delta.text}
            <span className="sr-only">{DIR_WORD[delta.dir]}</span>
          </span>
        )}
      </div>
      {plain && <div className="mt-1.5 text-[12px] leading-snug text-sub">{plain}</div>}
    </div>
  );
}
