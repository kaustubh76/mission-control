import { useTheme } from "../hooks/useTheme";
import StatusPill from "./ui/StatusPill";
import FreshnessPill from "./ui/FreshnessPill";

const VLAYER = "#7C5CFF";

/** vlayer verified seal — filled circle-check in the brand accent (shared motif). */
export function VlayerSeal({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0">
      <circle cx="12" cy="12" r="11" fill={VLAYER} fillOpacity="0.18" stroke={VLAYER} strokeWidth="1.5" />
      <path d="M6.8 12.4l3.2 3.2L17.2 8.3" stroke={VLAYER} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * Top header — wordmark + verifiable-provenance tagline, the "Verified by vlayer" pill,
 * live/stale freshness, an optional retry, and the light/dark theme toggle.
 */
export default function HeaderBar({
  live,
  lastTickTs,
  error,
  onRetry,
  vlayerState,
}: {
  live: boolean;
  lastTickTs: string | null;
  error?: unknown;
  onRetry?: () => void;
  vlayerState: "proven" | "pending" | "off";
}) {
  const { theme, toggle } = useTheme();
  const vlabel = vlayerState === "proven" ? "vlayer verified" : vlayerState === "pending" ? "vlayer · pending" : "vlayer";

  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-edge bg-panel2">
          <VlayerSeal size={20} />
        </span>
        <div className="leading-tight">
          <div className="font-display text-lg font-extrabold tracking-tight text-ink">Mission Control</div>
          <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted">
            verifiable data provenance · zkTLS
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone="vlayer" dot pulse={vlayerState === "proven"} srText={`vlayer ${vlayerState}`}>
          {vlabel}
        </StatusPill>
        <FreshnessPill live={live} lastTickTs={lastTickTs} />
        {!!error && onRetry && (
          <button
            onClick={onRetry}
            className="rounded-md border border-edge bg-panel2 px-2 py-0.5 font-mono text-[11px] text-sub transition hover:border-brand/40 hover:text-brand"
          >
            ↻ retry
          </button>
        )}
        <button
          onClick={toggle}
          aria-label="Toggle light/dark theme"
          title="Toggle theme"
          className="flex h-7 w-7 items-center justify-center rounded-md border border-edge bg-panel2 text-sub transition hover:border-brand/40 hover:text-brand"
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </div>
    </header>
  );
}
