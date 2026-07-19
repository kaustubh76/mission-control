import type { Health, Identity, Nav, Regime, State, Strategy } from "../api/types";
import { ageLabel, fgColor, fmtSignedPct, fmtUsd, regimeColor, shortAddr } from "../lib/format";
import { ddPlain, pnlSummary } from "../lib/pnl";
import AnimatedNumber from "./ui/AnimatedNumber";
import Card from "./ui/Card";
import CopyButton from "./ui/CopyButton";
import FreshnessPill from "./ui/FreshnessPill";
import Sparkline from "./ui/Sparkline";
import Stat from "./ui/Stat";
import StatusPill, { type Tone } from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

const ACCENT = "#7C5CFF"; // vlayer purple — NAV hero accent

function moodWord(fg: number | null | undefined): string {
  if (fg == null) return "uncertain";
  if (fg <= 24) return "fearful";
  if (fg <= 44) return "cautious";
  if (fg <= 55) return "neutral";
  if (fg <= 74) return "greedy";
  return "euphoric";
}

/** Named regime from the composite risk-on score (thresholds mirror regimeColor). */
function regimeLabel(s: number | null): { label: string; tone: Tone } {
  if (s == null) return { label: "NO SIGNAL", tone: "neutral" };
  if (s < 0.2) return { label: "RISK-OFF", tone: "down" };
  if (s < 0.5) return { label: "CAUTIOUS", tone: "warn" };
  if (s < 0.75) return { label: "NEUTRAL", tone: "info" };
  return { label: "RISK-ON", tone: "up" };
}

/** The deploy cap as a position in its regime-adaptive band: fill = current cap, shaded
 * region = [floor, ceiling], unfilled tail = the cash buffer. Makes the headline a visible
 * mechanism, not a lone lever. (Mirrors NavCard's DrawdownGauge track-fill-marker pattern.) */
function CapGauge({ capPct, floor, ceiling, color }: { capPct: number; floor: number; ceiling: number; color: string }) {
  const at = (v: number) => `${Math.min(100, Math.max(0, v))}%`;
  const f = floor * 100;
  const c = ceiling * 100;
  return (
    <div className="mt-4 border-t border-edge pt-3">
      <div className="mb-1 flex items-center justify-between text-[10px] text-muted">
        <span className="flex items-center gap-1">
          deploy band <InfoTip term="deployBand" />
        </span>
        <span className="font-mono">{Math.round(f)}–{Math.round(c)}%</span>
      </div>
      <div className="relative h-2.5 w-full rounded-full bg-edge">
        {/* adaptive band region [floor, ceiling] */}
        <div
          className="absolute inset-y-0 rounded-full bg-sub/15"
          style={{ left: at(f), right: `calc(100% - ${at(c)})` }}
        />
        {/* current deploy cap fill */}
        <div
          className="absolute inset-y-0 left-0 rounded-full transition-all duration-700"
          style={{ width: at(capPct), background: color }}
        />
        <span className="absolute -top-0.5 h-3.5 w-px bg-muted" style={{ left: at(f) }} />
        <span className="absolute -top-0.5 h-3.5 w-px bg-muted" style={{ left: at(c) }} />
      </div>
      <div className="mt-1 text-[10px] text-muted">regime-adaptive · {100 - capPct}% held in stables</div>
    </div>
  );
}

function secondsSince(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : (Date.now() - t) / 1000;
}
function secondsBetween(a: string | null | undefined, b: string | null | undefined): number | null {
  if (!a || !b) return null;
  const f = new Date(a).getTime();
  const t = new Date(b).getTime();
  return Number.isNaN(f) || Number.isNaN(t) ? null : (t - f) / 1000;
}

/**
 * TIER A — the three numbers a judge should grasp in five seconds:
 * how it's doing (NAV), how much risk it's taking right now (deploy cap), and
 * what it's actually allowed to do (status + on-chain proof).
 */
export default function HeroRow({
  nav,
  regime,
  state,
  health,
  freshness,
  identity,
  agentId,
  strategy,
  anchorUsd,
}: {
  nav: Nav;
  regime: Regime;
  state: State;
  health: Health | undefined;
  freshness: { lastTxTs: string | null; servedAt: string | null; live: boolean };
  identity: Identity | null | undefined;
  agentId: number | null;
  strategy?: Strategy | null;
  anchorUsd?: number | null;
}) {
  // ── Card 1 — NAV + today's move + drawdown headline ──
  // Anchor net-since-start to the campaign-start NAV so it reconciles with the P&L + Agent-economy cards.
  const { current, netPct, today, latestIsToday } = pnlSummary(nav, undefined, anchorUsd);
  const navValue = nav.current_nav ?? current;
  const dd = nav.drawdown.current;
  const ddColor = regimeColor(dd >= nav.caps.dq ? 0 : dd >= nav.caps.team ? 0.3 : 0.9);
  // Only show the NAV day-delta when the latest EOD point is actually today — otherwise the hero
  // would imply a stale move is "today's". When the campaign hasn't ticked today, show no delta.
  const todayDelta =
    today && latestIsToday
      ? { text: fmtSignedPct(today.pct), dir: today.pnl > 0 ? ("up" as const) : today.pnl < 0 ? ("down" as const) : ("flat" as const) }
      : undefined;
  const sparkData = (nav.curve ?? []).map((p) => p.nav);

  // ── Card 2 — Deploy cap + regime ──
  // Keep score nullable: regimeColor(null) → neutral gray, so a *missing* regime
  // score reads as "no signal" instead of being coerced to 0 → red ("risk-off").
  const score = regime.regime_score ?? null;
  const fg = regime.fear_greed;
  const cap = regime.deploy_cap ?? 0;
  const capPct = Math.round(cap * 100);
  const regPlain = `Market looks ${moodWord(fg)} → holding ${100 - capPct}% in stables`;
  const reg = regimeLabel(score);
  // Only draw the deploy band from REAL strategy params — never a plausible-but-wrong default band.
  const capFloor = strategy?.params.cap_floor ?? null;
  const capCeiling = strategy?.params.cap_ceiling ?? null;

  // ── Card 3 — Status + on-chain proof ──
  const live = health?.live_trading_enabled;
  const killed = health?.kill_switch_engaged;
  const halted = state.halted;
  const mode = halted
    ? { color: "#ea3943", headline: "Drawdown Halt", plain: "Risk limit hit — the agent is flat and not trading." }
    : killed
      ? { color: "#ea3943", headline: "Kill-Switch", plain: "Emergency halt — evaluations stopped, positions flat." }
      : live
        ? { color: "#f0b90b", headline: "Live-Armed", plain: "Cleared to sign real on-chain swaps via self-custody (TWAK)." }
        : { color: "#16c784", headline: "Live Track", plain: "The live on-chain trading book on free market data (Binance · DexScreener) — self-custodied via TWAK; not currently armed to sign." };

  const trades = state.cumulative_swaps ?? 0;
  const floor = state.trade_floor ?? 7;
  const floorMet = trades >= floor;
  const lastTxAge = freshness.live
    ? secondsSince(freshness.lastTxTs)
    : secondsBetween(freshness.lastTxTs, freshness.servedAt) ?? secondsSince(freshness.lastTxTs);
  const minted = (agentId ?? 0) > 0;
  const wallet = identity?.trading_wallet ?? null;
  const explorer = wallet
    ? `https://${identity?.network === "bsc-testnet" ? "testnet." : ""}bscscan.com/address/${wallet}`
    : null;

  return (
    <div id="sec-hero" data-section-label="Hero" className="grid scroll-mt-20 grid-cols-1 gap-4 md:grid-cols-3">
      {/* ① NAV */}
      <Card
        tier="hero"
        accent={ACCENT}
        right={
          <FreshnessPill
            live={freshness.live}
            lastTickTs={freshness.lastTxTs}
            liveLabel="LIVE"
            srLive="live on-chain book NAV"
          />
        }
      >
        <Stat
          label="Net Asset Value"
          term="nav"
          size="hero"
          color={ACCENT}
          glow
          value={<AnimatedNumber value={navValue} format={(n) => fmtUsd(n)} flash />}
          delta={todayDelta}
          plain={ddPlain(dd, nav.caps)}
        />
        <div className="mt-4 flex items-end justify-between gap-3 border-t border-edge pt-3">
          <div className="space-y-1 text-[11px]">
            <div className="flex items-center gap-1 text-muted">
              <span>net since start</span>
              <span className="font-mono" style={{ color: netPct >= 0 ? "#16c784" : "#ea3943" }}>
                {fmtSignedPct(netPct)}
              </span>
            </div>
            <div className="flex items-center gap-1 text-muted">
              <span>drawdown</span>
              <InfoTip term="drawdown" />
              <span className="font-mono" style={{ color: ddColor }}>
                {(dd * 100).toFixed(1)}%
              </span>
              <span className="opacity-70">/ {(nav.caps.team * 100).toFixed(0)}% cap</span>
            </div>
          </div>
          <Sparkline data={sparkData} color={ACCENT} height={32} className="w-24 shrink-0" />
        </div>
      </Card>

      {/* ② Deploy cap + regime */}
      <Card
        tier="hero"
        accent={regimeColor(score)}
        right={
          <span className="flex items-center gap-1.5">
            {regime.stale && (
              <StatusPill tone="warn" srText="cached fear and greed">CACHED F&G</StatusPill>
            )}
            <StatusPill tone={reg.tone} srText={`regime ${reg.label.toLowerCase()}`}>{reg.label}</StatusPill>
          </span>
        }
      >
        <Stat
          label="Deploy Cap"
          term="deployCap"
          size="hero"
          color={regimeColor(score)}
          glow
          value={<AnimatedNumber value={capPct} format={(n) => `${Math.round(n)}%`} flash />}
          plain={regPlain}
        />
        {capFloor != null && capCeiling != null && (
          <CapGauge capPct={capPct} floor={capFloor} ceiling={capCeiling} color={regimeColor(score)} />
        )}
        <div className="mt-3 grid grid-cols-2 gap-3 text-[11px]">
          <div className="flex items-center gap-1.5 text-muted">
            <span>risk-on</span>
            <InfoTip term="riskOnScore" />
            <span className="ml-auto font-mono" style={{ color: regimeColor(score) }}>
              {score != null ? <AnimatedNumber value={score} format={(n) => n.toFixed(2)} flash /> : "—"}
            </span>
          </div>
          <div className="flex items-center gap-1.5 text-muted">
            <span>fear &amp; greed</span>
            <InfoTip term="fearGreed" />
            <span className="ml-auto font-mono font-bold" style={{ color: fgColor(fg) }}>
              {fg != null ? <AnimatedNumber value={fg} format={(n) => `${Math.round(n)}`} flash /> : "—"}
            </span>
          </div>
        </div>
      </Card>

      {/* ③ Status + proof */}
      <Card tier="hero" accent={mode.color}>
        <div className="flex items-center gap-1.5">
          <span className="card-label">Agent Status</span>
          <InfoTip
            side="bottom"
            title="Agent status"
            text="What the agent is allowed to do right now, plus the on-chain proof it's real: trade count, last rebalance, and its wallet on BscScan."
          />
        </div>
        <div className="mt-1 font-display text-3xl font-bold leading-none md:text-4xl" style={{ color: mode.color }}>
          {mode.headline}
        </div>
        <div className="mt-1.5 text-[12px] leading-snug text-sub">{mode.plain}</div>

        <div className="mt-4 space-y-2 border-t border-edge pt-3 text-[11px]">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1 text-muted">
              trades to floor
              <InfoTip
                title="Trade floor"
                text="The book maintains at least 7 trades. The agent rebalances daily so it always clears the floor."
              />
            </span>
            <StatusPill tone={floorMet ? "up" : "warn"} srText={floorMet ? "floor met" : "below floor"}>
              {trades}/{floor}
            </StatusPill>
          </div>
          <div className="flex items-center justify-between text-muted">
            <span>last rebalance</span>
            <span className="font-mono text-sub">{ageLabel(lastTxAge)}</span>
          </div>
          <div className="flex items-center justify-between text-muted">
            <span className="flex items-center gap-1">
              identity <InfoTip term="erc8004" />
            </span>
            <StatusPill tone={minted ? "up" : "violet"} srText={minted ? "minted on-chain" : "configured, not yet minted"}>
              {minted ? `ERC-8004 #${agentId}` : "CONFIGURED"}
            </StatusPill>
          </div>
          {explorer && wallet && (
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <a href={explorer} target="_blank" rel="noreferrer" className="font-mono text-cyan hover:underline">
                  {shortAddr(wallet)}
                </a>
                <CopyButton text={wallet} />
              </span>
              <a href={explorer} target="_blank" rel="noreferrer" className="font-mono text-cyan hover:underline">
                view on BscScan ↗
              </a>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
