import type { TokenRotation } from "../api/types";
import { clockHM, tokenColor } from "../lib/format";
import { sourceLabel } from "../lib/rotation";
import Card from "./ui/Card";
import FreshnessPill from "./ui/FreshnessPill";
import InfoTip from "./ui/Tooltip";

/**
 * Token Rotation — which of the 8 contest tokens have actually been traded, and how.
 *
 * The momentum allocator only ever holds `top_k` (2) tokens; the contest-floor rotation reaches the
 * rest of the universe with tiny ~0-NAV round-trips so every token is touched over the week. This card
 * makes that visible WITHOUT overclaiming: a "floor" touch is explicitly labelled as the ≥1-trade
 * contest floor, never a momentum conviction. Data: `snapshot.token_rotation` (see token_rotation_card),
 * the live campaign coverage; empty-state until the first live tick populates it.
 */

/** Rotation headline + 8-token grid + footnote (no Card chrome). Reused standalone and in the merged
 * Token Universe · Rotation panel — where the grid widens to 8-across on the full-width layout. */
export function TokenRotationBody({ rotation }: { rotation: TokenRotation | null }) {
  const rot = rotation; // live campaign coverage (snapshot.token_rotation) — no sim fallback

  if (!rot || !rot.tokens.length) {
    return (
      <div className="flex items-center justify-center py-8 text-center text-xs text-muted">
        no rotation data yet
      </div>
    );
  }

  const { tokens, touched_count, total } = rot;
  const allTouched = touched_count >= total;

  return (
    <div>
      {/* Headline: N / total rotated */}
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted">universe coverage</span>
        <span className="font-display text-lg font-bold tabular-nums">
          <span className={allTouched ? "text-neon" : "text-ink"}>{touched_count}</span>
          <span className="text-muted">/{total}</span>
          <span className="ml-1.5 text-xs font-normal text-muted">rotated</span>
        </span>
      </div>

      {/* token grid — widens to 8-across on large screens so the full universe sits on one row */}
      <ul className="grid grid-cols-2 gap-1.5 text-xs sm:grid-cols-4 lg:grid-cols-8">
        {tokens.map((t) => {
          const touched = t.touched;
          const isHeld = t.source === "held" || t.source === "both";
          return (
            <li
              key={t.token}
              className={`flex flex-col gap-0.5 rounded-sm border px-2 py-1.5 transition ${
                touched ? "border-line bg-panel2" : "border-line/40 opacity-60"
              }`}
              title={
                t.last_ts
                  ? `${t.token} · ${sourceLabel(t.source)} · last ${clockHM(t.last_ts)} · ${t.count}×`
                  : `${t.token} · not yet touched`
              }
            >
              <span className="flex items-center justify-between">
                <span className="flex items-center gap-1.5">
                  <span
                    className="inline-block h-2.5 w-2.5 rounded-sm"
                    style={{ background: touched ? tokenColor(t.token) : "transparent", border: touched ? "none" : `1px solid ${tokenColor(t.token)}` }}
                  />
                  <span className={touched ? "text-ink" : "text-muted"}>{t.token}</span>
                </span>
                <span
                  className={touched ? "text-neon" : "text-muted"}
                  aria-label={touched ? "rotated" : "pending"}
                >
                  {touched ? "✓" : "○"}
                </span>
              </span>
              <span className="flex items-center justify-between font-mono text-[10px] text-sub">
                <span className={isHeld ? "text-brand" : "text-muted"}>{sourceLabel(t.source)}</span>
                {t.last_ts && <span className="text-muted">{clockHM(t.last_ts)}</span>}
              </span>
            </li>
          );
        })}
      </ul>

      {/* Honest footnote — the floor touches are NOT an edge claim */}
      <p className="mt-3 text-[10px] leading-snug text-muted">
        <span className="text-brand">momentum</span> = a real top-{2} holding ·{" "}
        <span className="text-sub">floor</span> = a ~0-NAV ≥1-trade nudge that rotates the rest of
        the universe. Coverage, not conviction — never an edge claim.
      </p>
    </div>
  );
}

export default function TokenRotationCard({
  rotation,
  live = true,
  lastTickTs,
}: {
  rotation: TokenRotation | null;
  live?: boolean;
  lastTickTs?: string | null;
}) {
  const right = (
    <span className="flex items-center gap-1.5">
      <InfoTip
        title="Token rotation"
        text="Which of the 8 tokens have been traded. The momentum arm holds only the top-2; the contest-floor rotation touches the rest with ~0-NAV round-trips. Coverage, not an edge claim."
      />
      <FreshnessPill live={live} lastTickTs={lastTickTs} srLive="live rotation" />
    </span>
  );

  return (
    <Card label="Token Rotation" accent="#7C5CFF" className="flex h-full flex-col" right={right}>
      <TokenRotationBody rotation={rotation} />
    </Card>
  );
}
