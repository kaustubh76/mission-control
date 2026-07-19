import type {
  RotationSource,
  Snapshot,
  TokenRotation,
  TokenRotationEntry,
} from "../api/types";

const EPS = 1e-9;

/**
 * Per-token rotation for the dashboard "Token Rotation" card.
 *
 * Prefers the backend `token_rotation` card (computed over the FULL journal incl. floor-nudge
 * `tokens`). If a snapshot predates that field, derive a HELD-ONLY view from the rebalance history
 * so the card never blanks — floor-nudge sources can't be reconstructed from old rows (they didn't
 * record which token was nudged), so the fallback only marks momentum holdings.
 */
export function rotationFromSnapshot(snap: Snapshot | null): TokenRotation | null {
  if (!snap) return null;
  if (snap.token_rotation && snap.token_rotation.tokens?.length) return snap.token_rotation;

  const universe = snap.strategy?.tokens ?? [];
  if (!universe.length) return null;

  const held = new Map<string, { count: number; last_ts: string | null }>();
  for (const r of snap.rebalances?.items ?? []) {
    for (const [tok, w] of Object.entries(r.weights_after ?? {})) {
      if (typeof w === "number" && w > EPS) {
        const e = held.get(tok) ?? { count: 0, last_ts: null };
        e.count += 1;
        e.last_ts = r.ts ?? e.last_ts;
        held.set(tok, e);
      }
    }
  }

  const tokens: TokenRotationEntry[] = universe.map((tok) => {
    const h = held.get(tok);
    return {
      token: tok,
      touched: !!h,
      source: (h ? "held" : "none") as RotationSource,
      count: h?.count ?? 0,
      last_ts: h?.last_ts ?? null,
    };
  });

  return {
    tokens,
    touched_count: tokens.filter((t) => t.touched).length,
    total: universe.length,
    held: [...held.keys()].sort(),
    nudged: [],
  };
}

/** Human label for a rotation source — kept honest (momentum vs ~0-NAV contest floor). */
export function sourceLabel(source: RotationSource): string {
  switch (source) {
    case "held":
      return "momentum";
    case "nudged":
      return "floor";
    case "both":
      return "momentum + floor";
    default:
      return "—";
  }
}
