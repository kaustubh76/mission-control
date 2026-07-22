import type { ReactNode } from "react";

export type Tone = "up" | "down" | "warn" | "armed" | "info" | "neutral" | "brand" | "violet" | "vlayer";

const TONE_COLOR: Record<Tone, string> = {
  up: "#16b981",
  down: "#ef4444",
  warn: "#f59e0b", // caution / risk only
  armed: "#f59e0b", // operational "live-armed" — same hex, distinct meaning from warn
  info: "#3861fb",
  neutral: "#6b7280",
  brand: "#7C5CFF", // vlayer purple — primary brand accent
  violet: "#8b9dff", // on-chain identity
  vlayer: "#7C5CFF", // vlayer brand — verifiable-data proof
};

interface StatusPillProps {
  tone?: Tone;
  /** Show a leading status dot. */
  dot?: boolean;
  /** Pulse the dot (live signals only). */
  pulse?: boolean;
  /** Visually-hidden clarifier so the status is never colour-only. */
  srText?: string;
  className?: string;
  children: ReactNode;
}

/**
 * The one status pill, consolidating the old per-panel Badge/Pill/Dot copies.
 * Always carries a text label (a11y) and an optional dot; square brutalist edges.
 */
export default function StatusPill({ tone = "neutral", dot, pulse, srText, className = "", children }: StatusPillProps) {
  const color = TONE_COLOR[tone];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wider ${className}`}
      style={{ color, borderColor: `${color}55`, background: `${color}12` }}
    >
      {dot && (
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${pulse ? "animate-pulseDot" : ""}`}
          style={{ background: color }}
        />
      )}
      {children}
      {srText && <span className="sr-only">{srText}</span>}
    </span>
  );
}
