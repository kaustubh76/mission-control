import type { AgentHub, Identity, Pillars } from "../api/types";
import { shortAddr } from "../lib/format";
import Card from "./ui/Card";
import CopyButton from "./ui/CopyButton";
import StatusPill, { type Tone } from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

/** One capability chip: a tone dot + label + small live detail. */
function Chip({ tone, label, detail }: { tone: Tone; label: string; detail: string }) {
  return (
    <div className="flex items-center gap-2 rounded-sm border border-edge bg-panel2/50 px-3 py-1.5">
      <StatusPill tone={tone} srText={label}>
        {label}
      </StatusPill>
      <span className="font-mono text-[10.5px] text-muted">{detail}</span>
    </div>
  );
}

/**
 * Compact "how it works + on-chain proof" strip — replaces the developer-facing
 * Three-Pillars and Agent-Hub panels. Every claim is asserted from LIVE data and
 * only when the capability is actually on, so it never shows the stale null-field
 * look the infra panels did, while keeping the core tech (data / TWAK / ERC-8004)
 * visible.
 */
export default function StackStrip({
  pillars,
  hub,
  identity,
  live = true,
}: {
  pillars: Pillars | null | undefined;
  hub: AgentHub | null | undefined;
  identity: Identity | null | undefined;
  live?: boolean;
}) {
  const twak = pillars?.twak;
  const nr = pillars?.nodereal;

  const minted = (nr?.agent_id ?? 0) > 0;
  const heartbeatLive = minted && !!nr?.heartbeat_enabled && !!nr?.sponsorable;
  const mcpCalls = hub?.mcp?.calls ?? 0;
  const wallet = identity?.trading_wallet ?? null;
  const explorer = wallet
    ? `https://${identity?.network === "bsc-testnet" ? "testnet." : ""}bscscan.com/address/${wallet}`
    : null;
  const prov = pillars?.commerce?.provenance;
  const provDetail =
    prov?.attestation === "onchain"
      ? "verified on-chain · zkTLS Web Proof"
      : prov?.enabled
        ? "provable · zkTLS Web Proof (pending)"
        : "verifiable data · zkTLS Web Proof";

  return (
    <Card
      label={
        <span className="inline-flex items-center gap-1">
          How it works · on-chain proof <InfoTip term="erc8004" side="bottom" />
        </span>
      }
      accent="#8b9dff"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Chip
          tone="up"
          label="Free data"
          detail="Binance · alternative.me · DexScreener · CoinGecko"
        />
        <Chip tone="up" label="TWAK-signed" detail={`self-custody · ${twak?.mode ?? "sim"}`} />
        <Chip
          tone={minted ? "up" : "violet"}
          label="ERC-8004"
          detail={
            minted
              ? `#${nr?.agent_id}${heartbeatLive ? " · gasless heartbeat" : ""}`
              : "identity configured"
          }
        />
        <Chip tone="vlayer" label="vlayer" detail={provDetail} />
        {mcpCalls > 0 && (
          <Chip tone="info" label="MCP" detail={`${mcpCalls} agent-hub calls`} />
        )}

        {explorer && wallet && (
          <span className="ml-auto flex items-center gap-1.5 font-mono text-[11px]">
            <a href={explorer} target="_blank" rel="noreferrer" className="text-cyan hover:underline">
              {shortAddr(wallet)} ↗
            </a>
            <CopyButton text={wallet} />
          </span>
        )}
      </div>

      <p className="mt-2.5 text-[11px] leading-snug text-muted">
        Reads free market data (Binance candles · alternative.me Fear&amp;Greed · DexScreener · CoinGecko)
        for regime &amp; market intel → sizes risk → signs spot swaps via Trust Wallet (TWAK),
        self-custodied → carries a verifiable ERC-8004 on-chain identity.
        {!live && <span className="ml-1 opacity-70">(showing the last cached snapshot)</span>}
      </p>
    </Card>
  );
}
