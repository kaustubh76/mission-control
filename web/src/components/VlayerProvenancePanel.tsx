import type { GlossaryKey } from "../lib/glossary";
import type { VlayerProvenance } from "../api/types";
import { shortAddr, ageLabel } from "../lib/format";
import Card from "./ui/Card";
import CopyButton from "./ui/CopyButton";
import StatusPill from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

// vlayer BRAND accent — electric purple, deliberately distinct from the #8b9dff on-chain-identity
// violet and the #3861fb CMC blue, so the proof layer reads unmistakably as *vlayer*.
const VLAYER = "#7C5CFF";

// Verified seal — a filled circle-check in the brand accent (stands in for a logo; none required).
function VlayerSeal({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0">
      <circle cx="12" cy="12" r="11" fill={VLAYER} fillOpacity="0.18" stroke={VLAYER} strokeWidth="1.5" />
      <path d="M6.8 12.4l3.2 3.2L17.2 8.3" stroke={VLAYER} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// "Verified by vlayer" — the vlayer wordmark rendered as branded text (lowercase, tracked, accent).
function VlayerTitle() {
  return (
    <span className="inline-flex items-center gap-1.5">
      <VlayerSeal />
      <span>
        Verified by{" "}
        <span className="font-bold lowercase tracking-wide" style={{ color: VLAYER }}>
          vlayer
        </span>
      </span>
      <InfoTip term="webproof" side="bottom" />
    </span>
  );
}

function Tile({ label, value, color, tip }: { label: string; value: string; color?: string; tip?: GlossaryKey }) {
  return (
    <div className="rounded-sm border border-edge bg-panel2 px-2.5 py-1.5">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted">
        {label}
        {tip && <InfoTip term={tip} />}
      </div>
      <div className="font-mono text-sm font-semibold leading-tight break-words" style={{ color: color ?? "rgb(var(--c-ink))" }}>
        {value}
      </div>
    </div>
  );
}

// Block explorer for the attestation chain (default: Optimism Sepolia, vlayer's default testnet).
function explorerBase(chain?: string | null): string {
  if (chain && chain.includes("optimism") && chain.includes("sepolia")) return "https://sepolia-optimism.etherscan.io";
  if (chain === "optimism") return "https://optimistic.etherscan.io";
  return "https://sepolia-optimism.etherscan.io";
}

/**
 * Verified by vlayer — surfaces the on-chain Web Proof (zkTLS) attestation that the agent's SOLD
 * Market Regime Report was built from data genuinely served by alternative.me (free, keyless).
 * vlayer-branded (electric purple + verified seal), read-only + key-free, so it renders on the
 * zero-secret cloud deploy. Degrades to a "pending" state until the first testnet attestation exists.
 */
export default function VlayerProvenancePanel({
  provenance,
  live = true,
}: {
  provenance?: VlayerProvenance | null;
  live?: boolean;
}) {
  const proven = provenance?.attestation === "onchain";
  const enabled = !!provenance?.enabled;

  const header = (
    <StatusPill tone={proven ? "vlayer" : enabled ? "neutral" : "neutral"} dot pulse={live && proven}>
      {proven ? "Verified" : enabled ? "Pending proof" : "off"}
    </StatusPill>
  );

  // Not proven yet (or disabled) — explain the capability + the path to a proof, never blank.
  if (!proven) {
    return (
      <Card label={<VlayerTitle />} accent={VLAYER} right={header}>
        <div className="space-y-2 text-xs text-muted">
          <div className="leading-snug">
            The sold <span className="text-sub">Market Regime Report</span>'s data provenance can be proven
            on-chain with a vlayer <span style={{ color: VLAYER }}>Web Proof (zkTLS)</span> — cryptographic
            evidence the inputs were genuinely served by alternative.me (free, keyless).
          </div>
          <div className="rounded-sm border px-2.5 py-1.5 text-[11px] leading-snug" style={{ borderColor: `${VLAYER}40`, background: `${VLAYER}0f` }}>
            {enabled
              ? "Enabled · awaiting the first attestation. Run the prover (vlayer/ → bun run prove:testnet) to publish one on Optimism Sepolia."
              : "Not configured. Set VLAYER_ENABLED + VLAYER_VERIFIER_ADDRESS after deploying the verifier."}
          </div>
        </div>
      </Card>
    );
  }

  const p = provenance!;
  const base = explorerBase(p.chain);
  const provenAgeS = p.proven_at ? (Date.now() - new Date(p.proven_at).getTime()) / 1000 : null;

  return (
    <Card label={<VlayerTitle />} accent={VLAYER} right={header}>
      <div className="space-y-3">
        {/* Branded proven-seal banner — reads unmistakably as a vlayer attestation, not a generic card. */}
        <div
          className="flex items-center gap-2 rounded-sm border px-2.5 py-2"
          style={{ borderColor: `${VLAYER}55`, background: `${VLAYER}12` }}
        >
          <VlayerSeal size={18} />
          <div className="leading-tight">
            <div className="text-[12px] font-bold" style={{ color: VLAYER }}>
              Proven on-chain
            </div>
            <div className="text-[10.5px] text-muted">
              alternative.me data provenance attested via vlayer Web Proof (zkTLS)
            </div>
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <Tile
            label="Fear & Greed"
            value={p.fear_greed != null ? `${p.fear_greed}${p.classification ? ` · ${p.classification}` : ""}` : "—"}
            color={VLAYER}
          />
          <Tile label="Proven" value={provenAgeS != null && Number.isFinite(provenAgeS) ? ageLabel(provenAgeS) : "—"} />
          <Tile label="Chain" value={p.chain ?? "optimism-sepolia"} />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-1.5 border-t border-edge pt-2 text-[11px]">
          <span className="text-muted">verifier</span>
          {p.verifier ? (
            <span className="flex items-center gap-1.5">
              <a href={`${base}/address/${p.verifier}`} target="_blank" rel="noreferrer" className="font-mono hover:underline" style={{ color: VLAYER }}>
                {shortAddr(p.verifier)} ↗
              </a>
              <CopyButton text={p.verifier} />
            </span>
          ) : (
            <span className="text-sub">—</span>
          )}
        </div>
        {p.agent && (
          <div className="flex items-center justify-between gap-1.5 text-[11px]">
            <span className="text-muted">agent</span>
            <a href={`${base}/address/${p.agent}`} target="_blank" rel="noreferrer" className="font-mono hover:underline" style={{ color: VLAYER }}>
              {shortAddr(p.agent)} ↗
            </a>
          </div>
        )}
      </div>
    </Card>
  );
}
