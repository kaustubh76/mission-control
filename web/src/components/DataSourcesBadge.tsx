/**
 * DataSourcesBadge — names the FREE, keyless data sources the agent runs on (CoinMarketCap is gone).
 * A small, reusable strip used wherever the UI previously asserted "CoinMarketCap". The source whose
 * provenance is attested on-chain (alternative.me, the Fear & Greed input) carries a vlayer seal.
 */

// vlayer brand accent — matches VlayerProvenancePanel / StatusPill "vlayer" tone.
const VLAYER = "#7C5CFF";

// Verified seal — filled circle-check in the brand accent (mirrors VlayerProvenancePanel's seal).
function VlayerSeal({ size = 11 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0">
      <circle cx="12" cy="12" r="11" fill={VLAYER} fillOpacity="0.18" stroke={VLAYER} strokeWidth="1.5" />
      <path d="M6.8 12.4l3.2 3.2L17.2 8.3" stroke={VLAYER} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const SOURCES: { label: string; href: string; vlayer?: boolean }[] = [
  { label: "Binance", href: "https://data-api.binance.vision" },
  { label: "alternative.me", href: "https://alternative.me/crypto/fear-and-greed-index/", vlayer: true },
  { label: "DexScreener", href: "https://dexscreener.com" },
  { label: "CoinGecko", href: "https://www.coingecko.com" },
];

export default function DataSourcesBadge({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1 text-[10px] text-muted">
      {!compact && <span className="uppercase tracking-wider">free data</span>}
      {SOURCES.map((s, i) => (
        <span key={s.label} className="inline-flex items-center gap-1">
          <a
            href={s.href}
            target="_blank"
            rel="noreferrer"
            className="rounded-sm bg-panel2 px-1.5 py-0.5 font-mono text-sub hover:text-cyan hover:underline"
          >
            {s.label}
          </a>
          {s.vlayer && (
            <span
              className="inline-flex items-center"
              title="Data provenance attested by a vlayer Web Proof (zkTLS)"
              aria-label="Provenance verified by vlayer"
            >
              <VlayerSeal />
            </span>
          )}
          {i < SOURCES.length - 1 && !compact && <span className="opacity-40">·</span>}
        </span>
      ))}
    </span>
  );
}
