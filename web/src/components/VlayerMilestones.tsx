import type { VlayerProvenance } from "../api/types";
import Card from "./ui/Card";
import StatusPill, { type Tone } from "./ui/StatusPill";
import { VlayerSeal } from "./HeaderBar";

const VLAYER = "#7C5CFF";

type Status = "done" | "next" | "after" | "stretch";
type Milestone = { id: string; title: string; scope: string; exit: string; status: Status; grant?: boolean };

const MILESTONES: Milestone[] = [
  {
    id: "M0",
    title: "Toolchain + scaffold",
    scope: "Prover/Verifier contracts, TS prove script, Python provenance bridge, dashboard panel — all building & tested locally.",
    exit: "Integration in-repo, consistent, tested",
    status: "done",
  },
  {
    id: "M1",
    title: "Web Proof e2e on Optimism Sepolia",
    scope: "Notarize a real alternative.me Fear & Greed response, run RegimeProver off-chain, submit the proof to RegimeVerifier.",
    exit: "An on-chain attestation transaction hash",
    status: "next",
    grant: true,
  },
  {
    id: "M2",
    title: "Bind the proof to the sold report",
    scope: "provenance.py reads the attestation; the sold ERC-8183 report carries a buyer-checkable on-chain provenance pointer.",
    exit: "A served job whose report links a live attestation",
    status: "after",
    grant: true,
  },
  {
    id: "M3",
    title: "Time Travel track record (stretch)",
    scope: "Make the agent's 13.2% worst-week drawdown claim verifiable against historical on-chain NAV heartbeats.",
    exit: "A verified drawdown attestation",
    status: "stretch",
    grant: true,
  },
  {
    id: "M4",
    title: "Dashboard + demo + docs",
    scope: "The branded 'Verified by vlayer' panel showing a live attestation + a reviewer demo video + finalized docs.",
    exit: "Live URL + demo video + submitted application",
    status: "after",
    grant: true,
  },
];

const STATUS_META: Record<Status, { tone: Tone; label: string }> = {
  done: { tone: "up", label: "done" },
  next: { tone: "vlayer", label: "next · needs vlayerup" },
  after: { tone: "neutral", label: "after M1" },
  stretch: { tone: "violet", label: "stretch" },
};

/**
 * Honest M0–M4 grant roadmap. M0 is shipped; M1 (the fundable core) is the first on-chain attestation,
 * gated only on installing the vlayer toolchain. Reflects the live state: once a real attestation exists,
 * M1 flips to done. Static content sourced from the grant docs — no fabricated progress.
 */
export default function VlayerMilestones({ provenance }: { provenance?: VlayerProvenance | null }) {
  const proven = provenance?.attestation === "onchain";
  return (
    <Card
      accent={VLAYER}
      label={
        <span className="inline-flex items-center gap-1.5">
          <VlayerSeal size={13} /> Grant roadmap · M0–M4
        </span>
      }
      right={
        <StatusPill tone="vlayer" srText="grant ask five to eight thousand dollars, milestone based">
          ask $5,000–$8,000 · milestone-based
        </StatusPill>
      }
    >
      <p className="mb-3 max-w-3xl text-sm leading-relaxed text-sub">
        vlayer becomes the trust layer of the agent economy:{" "}
        <span className="font-semibold text-ink">"trust me" agent commerce becomes "prove it."</span> The
        integration is built and dashboard-ready — the grant funds the first on-chain attestation (M1) through a
        demoed, buyer-verifiable proof (M4).
      </p>

      <ul className="space-y-2">
        {MILESTONES.map((m) => {
          const isM1Done = m.id === "M1" && proven;
          const meta = isM1Done ? STATUS_META.done : STATUS_META[m.status];
          return (
            <li key={m.id} className="flex flex-col gap-1.5 rounded-lg border border-edge bg-panel2 p-3 sm:flex-row sm:items-start sm:gap-3">
              <div className="flex shrink-0 items-center gap-2">
                <span
                  className="flex h-7 w-9 items-center justify-center rounded-md font-mono text-[12px] font-bold"
                  style={{ background: `${VLAYER}14`, color: VLAYER }}
                >
                  {m.id}
                </span>
                <StatusPill tone={meta.tone} dot pulse={isM1Done}>
                  {meta.label}
                </StatusPill>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 text-[13px] font-semibold text-ink">
                  {m.title}
                  {m.grant && (
                    <span className="rounded bg-panel px-1.5 py-px font-mono text-[9px] uppercase tracking-wider text-muted">
                      grant
                    </span>
                  )}
                </div>
                <p className="mt-0.5 text-[11.5px] leading-snug text-muted">{m.scope}</p>
                <p className="mt-0.5 font-mono text-[10.5px] text-sub">
                  <span className="text-muted">exit:</span> {m.exit}
                </p>
              </div>
            </li>
          );
        })}
      </ul>

      <div
        className="mt-3 rounded-lg border px-3 py-2 text-[11.5px] leading-snug text-sub"
        style={{ borderColor: `${VLAYER}33`, background: `${VLAYER}0d` }}
      >
        M1–M4 are gated only on installing the vlayer toolchain (<span className="font-mono">vlayerup</span>) + diffing
        the <span className="font-mono">kraken-web-proof</span> template. Until the first attestation lands, this
        dashboard truthfully shows <span className="font-semibold">pending</span> — never a fabricated proof.
      </div>
    </Card>
  );
}
