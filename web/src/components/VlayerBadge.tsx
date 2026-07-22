import type { VlayerProvenance } from "../api/types";
import StatusPill from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";
import { VlayerSeal } from "./HeaderBar";

export type ProvenanceState = "proven" | "pending" | "off";

/** Derive the honest vlayer state from the provenance block. */
export function provenanceState(p?: VlayerProvenance | null): ProvenanceState {
  return p?.attestation === "onchain" ? "proven" : p?.enabled ? "pending" : "off";
}

/**
 * Small reusable vlayer provenance marker — the one badge sprinkled across every panel that
 * surfaces the attested data (Fear & Greed from alternative.me / the sold Market Regime Report).
 * Honest by construction: proven → "vlayer verified"; pending → "vlayer · provable" (attestation
 * not yet on-chain); off → renders nothing. Never claims a proof that doesn't exist.
 */
export default function VlayerBadge({
  provenance,
  compact = false,
  withTip = true,
  className = "",
}: {
  provenance?: VlayerProvenance | null;
  compact?: boolean;
  withTip?: boolean;
  className?: string;
}) {
  const state = provenanceState(provenance);
  if (state === "off") return null;
  const proven = state === "proven";
  const label = compact ? (proven ? "vlayer ✓" : "vlayer") : proven ? "vlayer verified" : "vlayer · provable";
  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      <StatusPill
        tone="vlayer"
        dot
        pulse={proven}
        srText={proven ? "data provenance verified on-chain via vlayer" : "data provenance provable via a vlayer web proof; attestation pending"}
      >
        {label}
      </StatusPill>
      {withTip && <InfoTip term="webproof" side="bottom" />}
    </span>
  );
}

/**
 * An even smaller inline marker — just the seal + a title — for stamping a single data tile
 * (e.g. a Fear & Greed value) without a full pill.
 */
export function VlayerSealMark({ provenance, size = 12 }: { provenance?: VlayerProvenance | null; size?: number }) {
  const state = provenanceState(provenance);
  if (state === "off") return null;
  return (
    <span
      className="inline-flex items-center"
      title={
        state === "proven"
          ? "Provenance verified on-chain via a vlayer Web Proof (zkTLS)"
          : "Provenance provable via a vlayer Web Proof (zkTLS) — attestation pending"
      }
      aria-label="vlayer provenance"
    >
      <VlayerSeal size={size} />
    </span>
  );
}
