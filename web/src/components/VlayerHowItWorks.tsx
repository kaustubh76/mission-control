import type { ReactNode } from "react";
import type { VlayerProvenance } from "../api/types";
import type { GlossaryKey } from "../lib/glossary";
import Card from "./ui/Card";
import StatusPill from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";
import { VlayerSeal } from "./HeaderBar";

const VLAYER = "#7C5CFF";
const REPO = "https://github.com/kaustubh76/mission-control";
const FNG_URL = "https://api.alternative.me/fng/?limit=1";

type Step = { title: string; code: string; body: ReactNode; tip?: GlossaryKey };

const STEPS: Step[] = [
  {
    title: "Source",
    code: "api.alternative.me/fng",
    body: (
      <>
        The free, keyless <span className="font-semibold">Fear &amp; Greed</span> endpoint the agent reads —
        no API key, anyone can hit it.
      </>
    ),
  },
  {
    title: "Notarize",
    code: "prove.ts · createWebProof",
    body: <>A vlayer notary records the exact TLS session, capturing the response and the server's signature.</>,
    tip: "notarize",
  },
  {
    title: "Prove",
    code: "RegimeProver.main",
    body: (
      <>
        zkTLS proof that the transcript truly came from that URL; extracts Fear &amp; Greed (<code className="font-mono">require&nbsp;fg&nbsp;≤&nbsp;100</code>) + classification.
      </>
    ),
    tip: "webproof",
  },
  {
    title: "Verify on-chain",
    code: "RegimeVerifier.verify",
    body: (
      <>
        Only lands if the proof commits to those inputs — emits <span className="font-mono">RegimeProven</span> on
        Optimism Sepolia.
      </>
    ),
  },
  {
    title: "Bind to the sold report",
    code: "provenance.py · reportHash",
    body: (
      <>
        <span className="font-mono">provenance.py</span> reads it back; <span className="font-semibold">reportHash</span> ties
        the proof to the exact ERC-8183 report the agent sells.
      </>
    ),
    tip: "reportHash",
  },
];

const ARTIFACTS: { label: string; path: string }[] = [
  { label: "RegimeProver.sol", path: "vlayer/src/RegimeProver.sol" },
  { label: "RegimeVerifier.sol", path: "vlayer/src/RegimeVerifier.sol" },
  { label: "prove.ts", path: "vlayer/vlayer/prove.ts" },
  { label: "provenance.py", path: "src/ictbot/agent/provenance.py" },
  { label: "INTEGRATION_PLAN.md", path: "docs/vlayer/INTEGRATION_PLAN.md" },
  { label: "GRANT_APPLICATION.md", path: "docs/vlayer/GRANT_APPLICATION.md" },
];

/**
 * "How the Web Proof works" — a static, reviewer-facing pipeline of the real zkTLS flow
 * (alternative.me → notarize → RegimeProver → RegimeVerifier/RegimeProven on Optimism Sepolia →
 * provenance.py binds to the sold report), with "verify it yourself" links to the actual artifacts.
 * Uses the real contract/function names; no fabricated data.
 */
export default function VlayerHowItWorks({ provenance }: { provenance?: VlayerProvenance | null }) {
  const proven = provenance?.attestation === "onchain";
  return (
    <Card
      accent={VLAYER}
      label={
        <span className="inline-flex items-center gap-1.5">
          <VlayerSeal size={13} /> How the Web Proof works <InfoTip term="webproof" side="bottom" />
        </span>
      }
      right={
        <StatusPill tone={proven ? "vlayer" : "neutral"} dot pulse={proven}>
          {proven ? "live on-chain" : "zkTLS · Optimism Sepolia"}
        </StatusPill>
      }
    >
      <p className="mb-3 max-w-3xl text-sm leading-relaxed text-sub">
        The value the agent <span className="font-semibold text-ink">sells</span> is only as trustworthy as its
        inputs. vlayer <span style={{ color: VLAYER }} className="font-semibold">Web Proofs (zkTLS)</span> make the
        Fear &amp; Greed input <span className="font-semibold text-ink">cryptographically verifiable on-chain</span> —
        turning "trust me" into "prove it."
      </p>

      <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {STEPS.map((s, i) => (
          <li
            key={s.title}
            className="relative rounded-lg border border-edge bg-panel2 p-3"
            style={{ borderColor: i === 0 ? `${VLAYER}40` : undefined }}
          >
            <div className="mb-1 flex items-center gap-1.5">
              <span
                className="flex h-5 w-5 items-center justify-center rounded-full font-mono text-[11px] font-bold"
                style={{ background: `${VLAYER}18`, color: VLAYER }}
              >
                {i + 1}
              </span>
              <span className="text-[13px] font-semibold text-ink">{s.title}</span>
              {s.tip && <InfoTip term={s.tip} />}
            </div>
            <div className="mb-1.5 truncate font-mono text-[10.5px] text-brand" title={s.code}>
              {s.code}
            </div>
            <p className="text-[11.5px] leading-snug text-muted">{s.body}</p>
            {/* connector arrow (lg only) */}
            {i < STEPS.length - 1 && (
              <span className="pointer-events-none absolute -right-[11px] top-1/2 z-10 hidden -translate-y-1/2 text-muted lg:block">
                →
              </span>
            )}
          </li>
        ))}
      </ol>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-edge pt-2.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">verify it yourself</span>
        {ARTIFACTS.map((a) => (
          <a
            key={a.path}
            href={`${REPO}/blob/main/${a.path}`}
            target="_blank"
            rel="noreferrer"
            className="rounded-md bg-panel2 px-1.5 py-0.5 font-mono text-[11px] text-sub transition hover:text-brand hover:underline"
          >
            {a.label} ↗
          </a>
        ))}
      </div>
      <div className="mt-1.5 font-mono text-[10px] text-muted">
        proven endpoint: <span className="text-sub">{FNG_URL}</span>
      </div>
    </Card>
  );
}
