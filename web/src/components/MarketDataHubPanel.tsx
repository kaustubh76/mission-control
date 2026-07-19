import type { GlossaryKey } from "../lib/glossary";
import type { MarketDataHub } from "../api/types";
import { fgColor, regimeColor, cmcLabel } from "../lib/format";
import Card from "./ui/Card";
import DataSourcesBadge from "./DataSourcesBadge";
import StatusPill from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";

// Market-data cyan — distinguishes the free data feed from the violet on-chain/identity panels.
const DATA_CYAN = "#3861fb";

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

const pct = (n: number | null | undefined, dp = 1) => (n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(dp)}%`);
const changeColor = (n: number | null | undefined) => (n == null ? undefined : n >= 0 ? "#16c784" : "#ea3943");

/**
 * Market Data Hub — the live FREE-data-stack exhibit (replaces the retired CMC Agent Hub). Shows the
 * composed market-overview (regime, Fear & Greed, BTC dominance, mktcap 24h Δ, narratives) and the
 * per-token DexScreener signals the agent decides on. All keyless: Binance · alternative.me ·
 * DexScreener · CoinGecko. Read-only; renders on the 4s snapshot poll.
 */
export default function MarketDataHubPanel({
  hub,
  live = true,
}: {
  hub?: MarketDataHub | null;
  live?: boolean;
}) {
  const enabled = !!hub?.enabled;
  const ov = hub?.overview ?? null;
  const dex = hub?.dex ?? null;
  const dexEntries = dex ? Object.entries(dex) : [];

  const header = (
    <span className="flex items-center gap-1.5">
      <DataSourcesBadge compact />
      <StatusPill tone={enabled ? "up" : "neutral"} dot pulse={live && enabled}>
        {enabled ? "live · free" : "off"}
      </StatusPill>
    </span>
  );

  if (!enabled) {
    return (
      <Card label="Market Data Hub" accent={DATA_CYAN} right={header}>
        <div className="flex h-24 flex-col items-center justify-center gap-1 text-center text-xs text-muted">
          <div>Free market-data feeds are off in this deploy.</div>
          <div className="text-[10px] opacity-70">set FREE_DATA=true to stream Binance · alternative.me · DexScreener · CoinGecko</div>
        </div>
      </Card>
    );
  }

  return (
    <Card
      label={
        <span className="inline-flex items-center gap-1">
          Market Data Hub <InfoTip term="cmcPipeline" side="bottom" />
        </span>
      }
      accent={DATA_CYAN}
      right={header}
    >
      <div className="space-y-3">
        {/* Composed market-overview — the macro read behind the regime score, from free sources. */}
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
          <Tile
            label="Regime"
            value={ov?.regime ? cmcLabel(ov.regime) : "—"}
            color={regimeColor(ov?.risk_budget ?? null)}
          />
          <Tile label="Risk budget" value={ov?.risk_budget != null ? ov.risk_budget.toFixed(2) : "—"} />
          <Tile
            label="Fear & Greed"
            value={ov?.fear_greed != null ? String(ov.fear_greed) : "—"}
            color={fgColor(ov?.fear_greed)}
          />
          <Tile label="BTC dom" value={ov?.btc_dominance != null ? `${ov.btc_dominance.toFixed(1)}%` : "—"} />
          <Tile label="Mktcap 24h" value={pct(ov?.mktcap_change_24h)} color={changeColor(ov?.mktcap_change_24h)} />
        </div>

        {ov?.narratives && ov.narratives.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-[10px] uppercase tracking-wider text-muted">top movers</span>
            {ov.narratives.map((n) => (
              <span key={n} className="rounded-sm border border-edge bg-panel2 px-1.5 py-0.5 font-mono text-[10px] text-sub">
                {cmcLabel(n)}
              </span>
            ))}
          </div>
        )}

        {/* Per-token DexScreener signals — the live DEX-native data the agent trades on. */}
        {dexEntries.length > 0 && (
          <div className="space-y-1.5 border-t border-edge pt-2">
            <div className="text-[10px] uppercase tracking-wider text-muted">DEX signals · DexScreener</div>
            <div className="overflow-x-auto">
              <table className="w-full font-mono text-[11px]">
                <thead>
                  <tr className="text-[9px] uppercase tracking-wider text-muted">
                    <th className="py-0.5 pr-2 text-left">token</th>
                    <th className="py-0.5 pr-2 text-right">price</th>
                    <th className="py-0.5 pr-2 text-right">liquidity</th>
                    <th className="py-0.5 pr-2 text-right">24h vol</th>
                    <th className="py-0.5 pr-2 text-right">Δ24h</th>
                    <th className="py-0.5 text-right">Δ7d</th>
                  </tr>
                </thead>
                <tbody className="text-sub">
                  {dexEntries.map(([sym, d]) => (
                    <tr key={sym} className="border-t border-edge/40">
                      <td className="py-0.5 pr-2 text-left text-ink">{cmcLabel(sym)}</td>
                      <td className="py-0.5 pr-2 text-right">
                        {d.price_usd != null ? `$${d.price_usd.toLocaleString(undefined, { maximumSignificantDigits: 5 })}` : "—"}
                      </td>
                      <td className="py-0.5 pr-2 text-right">
                        {d.liquidity_usd != null ? `$${(d.liquidity_usd / 1e6).toFixed(1)}M` : "—"}
                      </td>
                      <td className="py-0.5 pr-2 text-right">
                        {d.volume_24h != null ? `$${(d.volume_24h / 1e6).toFixed(1)}M` : "—"}
                      </td>
                      <td className="py-0.5 pr-2 text-right" style={{ color: changeColor(d.price_change_h24) }}>
                        {pct(d.price_change_h24)}
                      </td>
                      <td className="py-0.5 text-right" style={{ color: changeColor(d.pct_7d) }}>
                        {pct(d.pct_7d)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
