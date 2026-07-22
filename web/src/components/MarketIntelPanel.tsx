import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";

import { getMarketIntel } from "../api/client";
import type { MarketIntel, RegimeTerms, VlayerProvenance } from "../api/types";
import { fgColor } from "../lib/format";
import type { GlossaryKey } from "../lib/glossary";
import Card from "./ui/Card";
import CheckButton from "./ui/CheckButton";
import DataSourcesBadge from "./DataSourcesBadge";
import VlayerBadge, { VlayerSealMark } from "./VlayerBadge";
import StatusPill from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

async function intelCheck() {
  const m = await getMarketIntel();
  if (!m.enabled) return { ok: false, detail: "Market intel off (CMC_INTEL_ENABLED=0)" };
  const cap = m.global_metrics?.total_market_cap;
  return {
    ok: !!m.global_metrics || !!m.regime_terms,
    detail: cap != null ? `mktcap $${(cap / 1e12).toFixed(2)}T live` : "enabled, no data yet",
  };
}

const UP = "#16c784";
const CMC_BLUE = "#3861fb";

/** Compact USD for trillions/billions (market caps). */
function fmtBig(n: number | null | undefined): string {
  if (n == null) return "—";
  const a = Math.abs(n);
  if (a >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toFixed(0)}`;
}

function Tile({ label, value, sub, color, tip }: { label: string; value: string; sub?: string; color?: string; tip?: GlossaryKey }) {
  return (
    <div className="rounded-sm border border-edge bg-panel2/40 px-3 py-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted">
        {label}
        {tip && <InfoTip term={tip} />}
      </div>
      <div className="font-mono text-lg font-semibold" style={{ color: color ?? "rgb(var(--c-ink))" }}>
        {value}
      </div>
      {sub && <div className="text-[10px] text-muted">{sub}</div>}
    </div>
  );
}

/** Stacked BTC / ETH / alt dominance bar. */
function DominanceBar({ btc, eth }: { btc: number | null; eth: number | null }) {
  const b = btc ?? 0;
  const e = eth ?? 0;
  const alt = Math.max(0, 100 - b - e);
  const seg = (w: number, color: string, label: string) =>
    w > 0 ? (
      <div
        className="flex h-full items-center justify-center overflow-hidden text-[9px] text-black/70"
        style={{ width: `${w}%`, background: color }}
        title={`${label} ${w.toFixed(1)}%`}
      >
        {w > 8 ? `${label} ${w.toFixed(0)}%` : ""}
      </div>
    ) : null;
  return (
    <div className="flex h-4 w-full overflow-hidden rounded-sm">
      {seg(b, "#f7931a", "BTC")}
      {seg(e, "#8b9dff", "ETH")}
      {seg(alt, "#23c4d6", "ALT")}
    </div>
  );
}

const TERM_LABELS: Record<keyof RegimeTerms, string> = {
  breadth: "breadth",
  trend: "trend",
  vol_factor: "vol brake",
  fng: "fear/greed",
  dominance: "btc dom",
  mktcap: "mktcap",
  fng_mom: "f&g mom",
  score: "score",
};

function RegimeBars({ terms }: { terms: RegimeTerms }) {
  const order: (keyof RegimeTerms)[] = ["breadth", "trend", "vol_factor", "fng", "dominance", "mktcap", "fng_mom"];
  const rows = order.filter((k) => terms[k] != null);
  return (
    <div className="space-y-1">
      {rows.map((k) => {
        const v = terms[k] as number;
        return (
          <div key={k} className="flex items-center gap-2 text-[10px]">
            <span className="w-16 shrink-0 text-muted">{TERM_LABELS[k]}</span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-panel2">
              <div className="h-full rounded-full" style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%`, background: CMC_BLUE }} />
            </div>
            <span className="w-8 text-right font-mono text-sub">{v.toFixed(2)}</span>
          </div>
        );
      })}
      {terms.score != null && (
        <div className="flex items-center justify-between border-t border-edge pt-1 text-[11px]">
          <span className="text-muted">risk-on score</span>
          <span className="font-mono font-semibold" style={{ color: UP }}>
            {(terms.score as number).toFixed(3)}
          </span>
        </div>
      )}
    </div>
  );
}

export default function MarketIntelPanel({
  intel,
  live = true,
  provenance,
}: {
  intel: MarketIntel | null | undefined;
  live?: boolean;
  provenance?: VlayerProvenance | null;
}) {
  // CMC-1: green "live" only when the regime is enabled AND the API is fresh — on the
  // static snapshot fallback (!live) show a muted "snapshot" badge, not green "live".
  const intelFresh = live && !!intel?.enabled;
  const attribution = <DataSourcesBadge />;

  const g = intel?.global_metrics;
  const fng = intel?.fng_trend ?? [];
  const latestFng = fng.length ? fng[fng.length - 1].value : null;
  const terms = intel?.regime_terms;
  const hasLive = !!g || fng.length > 0;

  return (
    <Card
      label="Market Intelligence"
      accent={CMC_BLUE}
      right={
        <span className="flex items-center gap-2">
          <VlayerBadge provenance={provenance} compact withTip={false} />
          <CheckButton label="refresh intel" run={intelCheck} />
          <StatusPill tone={intelFresh ? "up" : "neutral"} srText={intelFresh ? "live market data" : "snapshot"}>
            {intelFresh ? "LIVE" : intel?.enabled ? "SNAPSHOT" : "OFF"}
          </StatusPill>
          <span className="rounded-sm bg-[#3861fb22] px-2 py-0.5 font-display text-[10px] font-bold text-[#6e8bff]">FREE</span>
        </span>
      }
    >
      {!hasLive && !terms ? (
        <div className="flex h-24 flex-col items-center justify-center gap-1 text-xs text-muted">
          <span>Market intelligence is off</span>
          <span className="text-[10px] opacity-70">set CMC_INTEL_ENABLED=1 to stream global metrics</span>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          {/* A — Global metrics + dominance */}
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Tile label="total mktcap" value={fmtBig(g?.total_market_cap)} />
              <Tile label="24h volume" value={fmtBig(g?.total_volume_24h)} />
              <Tile label="btc dominance" value={g?.btc_dominance != null ? `${g.btc_dominance.toFixed(1)}%` : "—"} color="#f7931a" tip="btcDominance" />
              <Tile label="alt mktcap" value={fmtBig(g?.altcoin_market_cap)} color="#23c4d6" />
            </div>
            {g && (
              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wider text-muted">market dominance</div>
                <DominanceBar btc={g.btc_dominance} eth={g.eth_dominance} />
                <div className="mt-1 flex justify-between font-mono text-[9px]">
                  <span style={{ color: "#f7931a" }}>BTC {g.btc_dominance != null ? g.btc_dominance.toFixed(1) : "—"}%</span>
                  <span style={{ color: "#8b9dff" }}>ETH {g.eth_dominance != null ? g.eth_dominance.toFixed(1) : "—"}%</span>
                  <span style={{ color: "#23c4d6" }}>
                    ALT {g.btc_dominance != null && g.eth_dominance != null ? Math.max(0, 100 - g.btc_dominance - g.eth_dominance).toFixed(1) : "—"}%
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* B — Sentiment + regime breakdown */}
          <div className="space-y-3">
            <div>
              <div className="flex items-baseline justify-between">
                <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted">
                  fear &amp; greed (14d) <InfoTip term="fearGreed" /> <VlayerSealMark provenance={provenance} size={11} />
                </span>
                {latestFng != null && (
                  <span className="font-mono text-lg font-semibold" style={{ color: fgColor(latestFng) }}>
                    {latestFng}
                  </span>
                )}
              </div>
              <div className="h-14">
                {fng.length > 1 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={fng} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
                      <YAxis domain={[0, 100]} hide />
                      <Line
                        type="monotone"
                        dataKey="value"
                        stroke={fgColor(latestFng)}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex h-full items-center text-[10px] text-muted">no trend yet</div>
                )}
              </div>
            </div>
            {terms ? (
              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wider text-muted">regime breakdown</div>
                <RegimeBars terms={terms} />
              </div>
            ) : (
              <div className="text-[10px] text-muted">regime terms appear after an enhanced tick</div>
            )}
          </div>
        </div>
      )}
      <div className="mt-3 flex justify-end border-t border-edge pt-2">{attribution}</div>
    </Card>
  );
}
