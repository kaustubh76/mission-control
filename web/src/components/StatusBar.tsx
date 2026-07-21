import { useTheme } from "../hooks/useTheme";
import { ageLabel } from "../lib/format";
import { isTickStale } from "../lib/freshness";
import { OPEN_CHEATSHEET_EVENT } from "../lib/cockpit";
import { OPEN_PALETTE_EVENT } from "./CommandPalette";
import StatusPill, { type Tone } from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

/** Discoverable opener for the ⌘K command palette (dispatches a window event so it
 * needs no shared state with the palette). */
function PaletteChip() {
  return (
    <button
      onClick={() => window.dispatchEvent(new Event(OPEN_PALETTE_EVENT))}
      title="Command palette (⌘K)"
      aria-label="Open command palette"
      className="flex h-7 items-center gap-1 rounded-sm border border-edge bg-panel2 px-2 font-mono text-[10px] text-sub transition hover:border-cyan/60 hover:text-cyan focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
    >
      <span aria-hidden>⌘K</span>
    </button>
  );
}

/** Discoverable opener for the `?` keyboard-shortcuts cheatsheet. */
function HelpChip() {
  return (
    <button
      onClick={() => window.dispatchEvent(new Event(OPEN_CHEATSHEET_EVENT))}
      title="Keyboard shortcuts (?)"
      aria-label="Show keyboard shortcuts"
      className="flex h-7 w-7 items-center justify-center rounded-sm border border-edge bg-panel2 font-mono text-[11px] text-sub transition hover:border-cyan/60 hover:text-cyan focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
    >
      <span aria-hidden>?</span>
    </button>
  );
}

/** Compact dark/light toggle for the header. */
function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      onClick={toggle}
      title={`Switch to ${next} theme`}
      aria-label={`Switch to ${next} theme`}
      className="flex h-7 w-7 items-center justify-center rounded-sm border border-edge bg-panel2 text-sub transition hover:border-cyan/60 hover:text-cyan focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60"
    >
      <span aria-hidden className="text-sm leading-none">
        {theme === "dark" ? "☀" : "☾"}
      </span>
    </button>
  );
}

function secondsSince(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 1000;
}

/**
 * The top utility strip. Its whole job is to disambiguate the three different things
 * the old UI all called "live": the API CONNECTION, the DATA source (live vs the
 * committed demo snapshot), and the agent's trading MODE.
 */
export default function StatusBar({
  connection,
  freshness,
  onRetry,
}: {
  connection: { stale: boolean; error: string | null; lastUpdated: number | null };
  freshness?: { lastTxTs: string | null; servedAt: string | null; live: boolean };
  onRetry?: () => void;
}) {
  const fromLive = freshness?.live ?? true;
  const servedAge = secondsSince(freshness?.servedAt);
  // Data AGE = how long since the last rebalance tick (Class-B freshness), not how long since the poll.
  // This is the board's authoritative "is the data actually fresh" signal — see lib/freshness.ts.
  const tickAge = secondsSince(freshness?.lastTxTs);
  const tickStale = isTickStale(tickAge);

  // CONNECTION: only surfaced when the poll loop is degraded — a healthy LIVE link stays silent
  // to keep the header clean (the DATA pill already conveys freshness). Retry is one click away.
  const degraded = !!connection.error || connection.stale;
  const conn = connection.error
    ? { tone: "down" as Tone, label: "OFFLINE" }
    : { tone: "warn" as Tone, label: "STALE" };

  // DATA: live API payload, or the frozen snapshot.json fallback? When live, amber-flag a data
  // payload whose newest tick is older than the rebalance cadence so "LIVE DATA" never implies the
  // trading numbers are fresh when they're a day+ old.
  const dataPill = fromLive
    ? { tone: (tickStale ? "warn" : "up") as Tone, label: "LIVE DATA" }
    : { tone: "neutral" as Tone, label: "DEMO SNAPSHOT" };

  return (
    <div className="glow-card flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex items-center gap-2.5">
        <img
          src="/logo.svg"
          alt="Mission Control"
          className="h-10 w-auto sm:h-12"
          width={1000}
          height={200}
        />
        {/* vlayer-forward tagline — frames the product as verifiable data provenance, not just a trading agent. */}
        <span className="hidden border-l border-edge pl-2.5 text-[11px] leading-tight text-muted lg:inline">
          Verifiable data provenance ·{" "}
          <span className="font-semibold" style={{ color: "#7C5CFF" }}>vlayer</span> zkTLS Web Proofs
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {/* Connection — silent while healthy; surfaces a pill + retry only when the poll loop degrades. */}
        {degraded &&
          (onRetry ? (
            <button
              onClick={onRetry}
              title="click to re-poll the API now"
              aria-label={`connection ${conn.label.toLowerCase()} — click to retry`}
              className="flex min-h-[40px] cursor-pointer items-center rounded-sm transition hover:brightness-125 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/60 sm:min-h-0"
            >
              <StatusPill tone={conn.tone} dot srText={`connection ${conn.label.toLowerCase()}, click to retry`}>
                {conn.label}
              </StatusPill>
            </button>
          ) : (
            <StatusPill tone={conn.tone} dot srText={`connection ${conn.label.toLowerCase()}`}>
              {conn.label}
            </StatusPill>
          ))}

        <span className="flex items-center gap-1.5">
          <InfoTip
            side="bottom"
            title="Data source"
            text="LIVE DATA = streamed from the agent's API. DEMO SNAPSHOT = a frozen, real but offline capture used when the API isn't reachable."
          />
          <StatusPill
            tone={dataPill.tone}
            srText={
              fromLive
                ? `live data — last rebalance ${ageLabel(tickAge)}`
                : `demo snapshot ${ageLabel(servedAge)}`
            }
          >
            {dataPill.label}
            {fromLive && tickAge !== null && (
              <span className="ml-1 font-normal normal-case opacity-70">· ticked {ageLabel(tickAge)}</span>
            )}
            {!fromLive && servedAge !== null && (
              <span className="ml-1 font-normal normal-case opacity-70">· {ageLabel(servedAge)}</span>
            )}
          </StatusPill>
        </span>

        <PaletteChip />
        <HelpChip />
        <ThemeToggle />
      </div>
    </div>
  );
}
