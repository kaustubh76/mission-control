import type { UseAllocator } from "../hooks/useAllocator";
import type { Snapshot } from "../api/types";
import { sectionId } from "../lib/sections";
import { fmtUsd, fmtSignedPct, fgColor } from "../lib/format";
import { pnlSummary } from "../lib/pnl";
import { rotationFromSnapshot } from "../lib/rotation";
import type { ReactNode } from "react";

import HeaderBar from "./HeaderBar";
import StatusTicker from "./StatusTicker";
import ProvenanceHero from "./ProvenanceHero";
import VlayerHowItWorks from "./VlayerHowItWorks";
import VlayerMilestones from "./VlayerMilestones";
import AgentCommercePanel from "./AgentCommercePanel";
import Cheatsheet from "./Cheatsheet";
import CommandPalette from "./CommandPalette";
import MarketDataHubPanel from "./MarketDataHubPanel";
import ControlPanel from "./ControlPanel";
import KeyboardLayer from "./KeyboardLayer";
import EconomyPnLCard from "./EconomyPnLCard";
import AutoSelectorCard from "./AutoSelectorCard";
import SchedulerCard from "./SchedulerCard";
import EquityCurve from "./EquityCurve";
import IdentityCard from "./IdentityCard";
import LiveArmCard from "./LiveArmCard";
import LiveWalletCard from "./LiveWalletCard";
import MarketIntelPanel from "./MarketIntelPanel";
import NavCard from "./NavCard";
import PnLCard from "./PnLCard";
import RationaleTicker from "./RationaleTicker";
import RebalanceTable from "./RebalanceTable";
import StackStrip from "./StackStrip";
import SystemDiagnostics from "./SystemDiagnostics";
import StrategySelectPanel from "./StrategySelectPanel";
import Tour from "./Tour";
import TokenUniversePanel from "./TokenUniversePanel";
import Card from "./ui/Card";
import Stat from "./ui/Stat";
import VlayerBadge from "./VlayerBadge";
import Collapsible from "./ui/Collapsible";
import ErrorBoundary from "./ui/ErrorBoundary";
import DashboardSkeleton from "./ui/Skeleton";

const VLAYER = "#7C5CFF";

/** Wrap a panel so a single render failure degrades to a notice, never a blank app.
 * Also stamps a scroll-target id + label so the command palette can jump to it. */
function Panel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <ErrorBoundary label={label}>
      <div id={sectionId(label)} data-section-label={label} className="h-full scroll-mt-20">
        {children}
      </div>
    </ErrorBoundary>
  );
}

/** The four headline metrics, monospace figures — NAV · PnL · Regime · Agent. */
function KpiRow({ data }: { data: Snapshot }) {
  const navVal = data.nav?.current_nav ?? data.state?.nav ?? 0;
  let netPct = 0;
  try {
    netPct = pnlSummary(data.nav, undefined, data.economy?.anchor_usd ?? null).netPct;
  } catch {
    /* keep 0 */
  }
  const health = data.health;
  const agentLabel = health?.kill_switch_engaged ? "halted" : health?.ok ? "live" : "down";
  const agentColor = health?.kill_switch_engaged ? "#ef4444" : health?.ok ? "#16b981" : "#f59e0b";
  const fg = data.regime?.fear_greed;

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Card>
        <Stat label="Net Asset Value" value={fmtUsd(navVal)} color={VLAYER} glow plain="live book value" />
      </Card>
      <Card>
        <Stat
          label="P&L · net"
          value={fmtSignedPct(netPct)}
          color={netPct >= 0 ? "#16b981" : "#ef4444"}
          plain="since campaign start"
        />
      </Card>
      <Card right={<VlayerBadge provenance={data.pillars?.commerce?.provenance} compact withTip={false} />}>
        <Stat
          label="Regime"
          value={fg != null ? String(fg) : "—"}
          color={fg != null ? fgColor(fg) : undefined}
          plain={data.regime?.fear_greed_label ?? "fear & greed"}
        />
      </Card>
      <Card>
        <Stat label="Agent" value={agentLabel} color={agentColor} plain={`mode ${health?.mode ?? "—"}`} />
      </Card>
    </div>
  );
}

export default function MissionControl({ allocator }: { allocator: UseAllocator }) {
  const { data, error, stale, live } = allocator;

  if (!data) {
    // First load / Render cold-start: shimmer skeleton instead of a blank screen.
    return (
      <>
        <DashboardSkeleton message={error ? "can't reach the agent — retrying…" : "connecting to agent…"} />
        {error && (
          <div className="fixed inset-x-0 bottom-4 flex justify-center">
            <button
              onClick={() => void allocator.refresh()}
              className="rounded-lg border border-brand/40 bg-brand/10 px-4 py-2 font-mono text-sm font-bold text-brand shadow-brut transition hover:bg-brand/20"
            >
              ↻ Retry connection
            </button>
          </div>
        )}
      </>
    );
  }

  const freshness = {
    lastTxTs: data.rebalances.items[0]?.ts ?? null,
    servedAt: data.served_at ?? null,
    live,
  };
  const prov = data.pillars?.commerce?.provenance;
  const vlayerState: "proven" | "pending" | "off" =
    prov?.attestation === "onchain" ? "proven" : prov?.enabled ? "pending" : "off";

  return (
    <div className="mx-auto max-w-[1400px] space-y-5 overflow-x-clip p-4 md:p-6">
      <CommandPalette allocator={allocator} />
      <KeyboardLayer allocator={allocator} />
      <Cheatsheet />
      <Tour />

      {/* ── Header + quant-terminal status ticker ── */}
      <ErrorBoundary label="Header" fallback={null}>
        <HeaderBar
          live={live}
          lastTickTs={freshness.lastTxTs}
          error={stale ? error : undefined}
          onRetry={() => void allocator.refresh()}
          vlayerState={vlayerState}
        />
      </ErrorBoundary>
      <ErrorBoundary label="Status ticker" fallback={null}>
        <StatusTicker data={data} live={live} />
      </ErrorBoundary>

      {/* ── Provenance hero — verifiable data provenance leads the page ── */}
      <ErrorBoundary label="Verified by vlayer">
        <div id={sectionId("Verified by vlayer")} data-section-label="Verified by vlayer" className="scroll-mt-20">
          <ProvenanceHero provenance={prov} regime={data.regime} live={live} />
        </div>
      </ErrorBoundary>

      {/* ── How the vlayer Web Proof works — reviewer-facing pipeline ── */}
      <Panel label="How the Web Proof works">
        <VlayerHowItWorks provenance={prov} />
      </Panel>

      {/* ── KPI row — the headline numbers ── */}
      <ErrorBoundary label="Key metrics">
        <KpiRow data={data} />
      </ErrorBoundary>

      {/* ── Performance, funds & market context ── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-8">
          <Panel label="Equity curve">
            <EquityCurve nav={data.nav} live={live} lastTickTs={freshness.lastTxTs} candleSource={data.rebalances.items[0]?.candle_source} />
          </Panel>
        </div>
        <div className="lg:col-span-4">
          <Panel label="NAV">
            <NavCard nav={data.nav} state={data.state} live={live} lastTickTs={freshness.lastTxTs} />
          </Panel>
        </div>

        <div className="lg:col-span-6">
          <Panel label="P&L">
            <PnLCard nav={data.nav} live={live} lastTickTs={freshness.lastTxTs} anchorUsd={data.economy?.anchor_usd ?? null} />
          </Panel>
        </div>
        <div className="lg:col-span-6">
          <Panel label="Wallet">
            <LiveWalletCard wallet={data.wallet} state={data.state} live={live} />
          </Panel>
        </div>

        <div className="lg:col-span-12">
          <Panel label="Agent economy">
            <EconomyPnLCard economy={data.economy} commerce={data.pillars?.commerce} />
          </Panel>
        </div>

        <div className="lg:col-span-12">
          <Panel label="Market intelligence">
            <MarketIntelPanel intel={data.market_intel} live={live} provenance={prov} />
          </Panel>
        </div>

        <div className="lg:col-span-12">
          <Panel label="Market Data Hub">
            <MarketDataHubPanel hub={data.market_data_hub} live={live} provenance={prov} />
          </Panel>
        </div>

        {/* Agent Commerce — the SELL side (ERC-8183) whose report provenance the hero attests. */}
        <div className="lg:col-span-12">
          <Panel label="Agent Commerce">
            <AgentCommercePanel commerce={data.pillars?.commerce} live={live} />
          </Panel>
        </div>
      </div>

      {/* ── vlayer grant roadmap (M0–M4) ── */}
      <Panel label="Grant roadmap">
        <VlayerMilestones provenance={prov} />
      </Panel>

      {/* ── Detail & on-chain proof (collapsible) ── */}
      <Collapsible title="Detail & Proof" id="detail-band" defaultOpen>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <Panel label="Rebalances">
              <RebalanceTable rebalances={data.rebalances} />
            </Panel>
          </div>
          <div className="lg:col-span-5">
            <Panel label="Rationale">
              <RationaleTicker feed={data.rationale} />
            </Panel>
          </div>

          <div className="lg:col-span-12">
            <Panel label="Token universe">
              <TokenUniversePanel rotation={rotationFromSnapshot(data)} live={live} lastTickTs={freshness.lastTxTs} />
            </Panel>
          </div>

          <div className="lg:col-span-12">
            <Panel label="Live arm">
              <LiveArmCard liveArm={data.live_arm} />
            </Panel>
          </div>

          <div className="lg:col-span-12">
            <Panel label="Scheduler">
              <SchedulerCard sched={data.scheduler} />
            </Panel>
          </div>

          <div className="lg:col-span-12">
            <Panel label="Auto-selector">
              <AutoSelectorCard sel={data.auto_selector} />
            </Panel>
          </div>

          <div className="lg:col-span-12">
            <Panel label="Strategy Lab">
              <StrategySelectPanel allocator={allocator} />
            </Panel>
          </div>

          <div className="lg:col-span-12">
            <Panel label="System diagnostics">
              <SystemDiagnostics allocator={allocator} />
            </Panel>
          </div>

          <div className="lg:col-span-12">
            <Panel label="Tech stack & proof">
              <StackStrip pillars={data.pillars} hub={data.agent_hub} identity={data.identity} live={live} />
            </Panel>
          </div>

          <div className="lg:col-span-6">
            <Panel label="Identity">
              <IdentityCard
                identity={data.identity}
                agentId={data.pillars?.nodereal?.agent_id ?? null}
                nodereal={data.pillars?.nodereal}
              />
            </Panel>
          </div>
          <div className="lg:col-span-6">
            <Panel label="Controls">
              <ControlPanel allocator={allocator} />
            </Panel>
          </div>
        </div>
      </Collapsible>

      <footer className="pb-2 pt-1 text-center font-mono text-[11px] text-muted">
        vlayer-verified data provenance · free data: Binance · alternative.me · DexScreener · CoinGecko · polling every 4s
      </footer>
    </div>
  );
}
