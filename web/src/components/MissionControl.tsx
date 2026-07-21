import type { UseAllocator } from "../hooks/useAllocator";
import { sectionId } from "../lib/sections";
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
import HeroRow from "./HeroRow";
import IdentityCard from "./IdentityCard";
import LiveArmCard from "./LiveArmCard";
import LiveWalletCard from "./LiveWalletCard";
import MarketIntelPanel from "./MarketIntelPanel";
import NavCard from "./NavCard";
import PnLCard from "./PnLCard";
import RationaleTicker from "./RationaleTicker";
import RebalanceTable from "./RebalanceTable";
import StackStrip from "./StackStrip";
import VlayerProvenancePanel from "./VlayerProvenancePanel";
import StatusBar from "./StatusBar";
import SystemDiagnostics from "./SystemDiagnostics";
import StrategySelectPanel from "./StrategySelectPanel";
import Tour from "./Tour";
import TokenUniversePanel from "./TokenUniversePanel";
import Collapsible from "./ui/Collapsible";
import ErrorBoundary from "./ui/ErrorBoundary";
import DashboardSkeleton from "./ui/Skeleton";
import { rotationFromSnapshot } from "../lib/rotation";
import type { ReactNode } from "react";

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

export default function MissionControl({ allocator }: { allocator: UseAllocator }) {
  const { data, error, stale, lastUpdated, live } = allocator;

  if (!data) {
    // First load / Render cold-start: shimmer skeleton instead of a blank screen.
    // If the API is unreachable AND we have no data at all, offer a retry inline.
    return (
      <>
        <DashboardSkeleton message={error ? "can't reach the agent — retrying…" : "connecting to agent…"} />
        {error && (
          <div className="fixed inset-x-0 bottom-4 flex justify-center">
            <button
              onClick={() => void allocator.refresh()}
              className="rounded-sm border-3 border-cool/50 bg-cool/10 px-4 py-2 font-display text-sm font-bold text-cyan shadow-brut-sm transition hover:bg-cool/20"
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

  return (
    <div className="mx-auto max-w-[1500px] space-y-6 overflow-x-clip p-4 md:p-6">
      <CommandPalette allocator={allocator} />
      <KeyboardLayer allocator={allocator} />
      <Cheatsheet />
      <Tour />
      {/* ── Utility strip: disambiguate connection / data / mode ── */}
      <ErrorBoundary label="Status bar" fallback={null}>
        <StatusBar
          connection={{ stale, error, lastUpdated }}
          freshness={freshness}
          onRetry={() => void allocator.refresh()}
        />
      </ErrorBoundary>

      {/* ── TIER A — the three numbers that matter ── */}
      <ErrorBoundary label="Hero">
        <HeroRow
          nav={data.nav}
          regime={data.regime}
          state={data.state}
          health={data.health}
          freshness={freshness}
          identity={data.identity}
          agentId={data.pillars?.nodereal?.agent_id ?? null}
          strategy={data.strategy}
          anchorUsd={data.economy?.anchor_usd ?? null}
        />
      </ErrorBoundary>

      {/* ── TIER B — supporting performance, funds & market context ── */}
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

        {/* Consolidated agent-economy P&L — the single "true position" across trading + commerce − x402. */}
        <div className="lg:col-span-12">
          <Panel label="Agent economy">
            <EconomyPnLCard economy={data.economy} commerce={data.pillars?.commerce} />
          </Panel>
        </div>

        <div className="lg:col-span-12">
          <Panel label="Market intelligence">
            <MarketIntelPanel intel={data.market_intel} live={live} />
          </Panel>
        </div>

        {/* Market Data Hub — the live FREE-data-stack exhibit: composed market-overview (regime,
            Fear & Greed, BTC dominance, mktcap 24h) + per-token DexScreener signals. Replaces the
            legacy CMC Agent Hub (CmcAgentHubPanel.tsx kept in-repo, unused, as a historical exhibit). */}
        <div className="lg:col-span-12">
          <Panel label="Market Data Hub">
            <MarketDataHubPanel hub={data.market_data_hub} live={live} />
          </Panel>
        </div>

        {/* Agent Commerce — the SELL side (ERC-8183): the agent monetizes its market analysis.
            With the Market Data Hub above (the free-data inputs) this is the agent-economy exhibit. */}
        <div className="lg:col-span-12">
          <Panel label="Agent Commerce">
            <AgentCommercePanel commerce={data.pillars?.commerce} live={live} />
          </Panel>
        </div>

        {/* Verified by vlayer — the proof layer that attests the SOLD report's data provenance.
            Elevated out of the collapsible detail band into the main grid (above the fold) so
            verifiable provenance reads as the headline, not a footnote. Shows the honest
            "attestation pending" state until the first on-chain proof (M1) lands. */}
        <div className="lg:col-span-12">
          <Panel label="Verified by vlayer">
            <VlayerProvenancePanel provenance={data.pillars?.commerce?.provenance} live={live} />
          </Panel>
        </div>
      </div>

      {/* ── TIER C — collapsible detail & on-chain proof ── */}
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

          {/* Scheduler health — catches a silently-dead cron (live tick / drawdown-watch age). */}
          <div className="lg:col-span-12">
            <Panel label="Scheduler">
              <SchedulerCard sched={data.scheduler} />
            </Panel>
          </div>

          {/* Forward-gated auto-selector — ranks DQ-safe arms by risk-adjusted forward score + recommends
              the live arm (recommend-only; anti-chasing hysteresis). */}
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
        Live on-chain deployment · free data: Binance · alternative.me · DexScreener · CoinGecko · polling every 4s
      </footer>
    </div>
  );
}
