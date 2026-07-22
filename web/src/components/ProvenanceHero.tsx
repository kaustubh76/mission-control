import type { VlayerProvenance, Regime } from "../api/types";
import { shortAddr, ageLabel, fgColor } from "../lib/format";
import Card from "./ui/Card";
import StatusPill from "./ui/StatusPill";
import CopyButton from "./ui/CopyButton";
import InfoTip from "./ui/Tooltip";
import { VlayerSeal } from "./HeaderBar";

const VLAYER = "#7C5CFF";

function explorerBase(chain?: string | null): string {
  if (chain === "optimism") return "https://optimistic.etherscan.io";
  return "https://sepolia-optimism.etherscan.io";
}

function Tile({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-lg border border-edge bg-panel2 px-3 py-2">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">{label}</div>
      <div className="mt-0.5 font-mono text-sm font-bold leading-tight text-ink" style={color ? { color } : undefined}>
        {value}
      </div>
    </div>
  );
}

/**
 * ProvenanceHero — the page's leading banner. Frames the product around vlayer verifiable
 * data provenance: the SOLD Market Regime Report's inputs can be proven on-chain with a
 * vlayer Web Proof (zkTLS). Shows the HONEST state — "Proven on-chain" only when a real
 * attestation exists, otherwise "Attestation pending". Never fabricates a proof.
 */
export default function ProvenanceHero({
  provenance,
  regime,
  live = true,
}: {
  provenance?: VlayerProvenance | null;
  regime: Regime;
  live?: boolean;
}) {
  const proven = provenance?.attestation === "onchain";
  const enabled = !!provenance?.enabled;
  const fg = provenance?.fear_greed ?? regime?.fear_greed ?? null;
  const fgLabel = provenance?.classification ?? regime?.fear_greed_label ?? null;
  const chain = provenance?.chain ?? "optimism-sepolia";
  const base = explorerBase(provenance?.chain);
  const provenAgeS = provenance?.proven_at ? (Date.now() - new Date(provenance.proven_at).getTime()) / 1000 : null;

  const headline = proven ? "Proven on-chain" : enabled ? "Attestation pending" : "Integration ready";
  const statePill = (
    <StatusPill tone={proven ? "vlayer" : "neutral"} dot pulse={live && proven} srText={`vlayer ${proven ? "proven" : enabled ? "pending" : "off"}`}>
      {proven ? "Verified" : enabled ? "Pending proof" : "off"}
    </StatusPill>
  );

  return (
    <Card
      tier="hero"
      accent={VLAYER}
      label={
        <span className="inline-flex items-center gap-1.5">
          <VlayerSeal size={14} />
          Verified by <span className="font-bold lowercase" style={{ color: VLAYER }}>vlayer</span>
          <InfoTip term="webproof" side="bottom" />
        </span>
      }
      right={statePill}
    >
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Left: the headline claim */}
        <div className="lg:col-span-2">
          <div className="flex items-center gap-2">
            <VlayerSeal size={26} />
            <h1 className="font-display text-2xl font-extrabold tracking-tight text-ink sm:text-3xl">
              {headline}
            </h1>
          </div>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-sub">
            The agent's <span className="font-semibold text-ink">Market Regime Report</span> — which it{" "}
            <span className="font-semibold text-ink">sells</span> to other agents — has its data provenance
            proven with a vlayer{" "}
            <span className="font-semibold" style={{ color: VLAYER }}>Web Proof (zkTLS)</span>: cryptographic
            evidence the Fear &amp; Greed input was genuinely served by{" "}
            <a
              href="https://alternative.me/crypto/fear-and-greed-index/"
              target="_blank"
              rel="noreferrer"
              className="font-semibold underline decoration-dotted underline-offset-2 hover:text-brand"
            >
              alternative.me
            </a>{" "}
            (free, keyless). {proven ? "" : "Turn “trust me” agent commerce into “prove it.”"}
          </p>

          {!proven && (
            <div
              className="mt-3 rounded-lg border px-3 py-2 text-[12px] leading-snug text-sub"
              style={{ borderColor: `${VLAYER}33`, background: `${VLAYER}0d` }}
            >
              {enabled
                ? "Integration live · awaiting the first attestation. Running the prover (vlayer/ → bun run prove:testnet) publishes it on Optimism Sepolia — this panel then upgrades to a linked on-chain proof."
                : "Set VLAYER_ENABLED + VLAYER_VERIFIER_ADDRESS after deploying the verifier to light this up."}
            </div>
          )}

          {proven && provenance?.verifier && (
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-edge pt-2 text-[12px]">
              <span className="inline-flex items-center gap-1.5">
                <span className="font-mono text-muted">verifier</span>
                <a href={`${base}/address/${provenance.verifier}`} target="_blank" rel="noreferrer" className="font-mono hover:underline" style={{ color: VLAYER }}>
                  {shortAddr(provenance.verifier)} ↗
                </a>
                <CopyButton text={provenance.verifier} />
              </span>
              {provenance.agent && (
                <span className="inline-flex items-center gap-1.5">
                  <span className="font-mono text-muted">agent</span>
                  <a href={`${base}/address/${provenance.agent}`} target="_blank" rel="noreferrer" className="font-mono hover:underline" style={{ color: VLAYER }}>
                    {shortAddr(provenance.agent)} ↗
                  </a>
                </span>
              )}
            </div>
          )}
        </div>

        {/* Right: the proof facts */}
        <div className="grid grid-cols-2 gap-2 lg:col-span-1">
          <Tile label="Source" value="alternative.me" color={VLAYER} />
          <Tile
            label="Fear & Greed"
            value={fg != null ? `${fg}${fgLabel ? ` · ${fgLabel}` : ""}` : "—"}
            color={fg != null ? fgColor(fg) : undefined}
          />
          <Tile label="Method" value="zkTLS Web Proof" />
          <Tile label={proven ? "Proven" : "Chain"} value={proven && provenAgeS != null ? ageLabel(provenAgeS) : chain} />
        </div>
      </div>
    </Card>
  );
}
