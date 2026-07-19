/**
 * DataSourcesBadge — names the FREE, keyless data sources the agent runs on (CoinMarketCap is gone).
 * A small, reusable strip used wherever the UI previously asserted "CoinMarketCap".
 */
const SOURCES: { label: string; href: string }[] = [
  { label: "Binance", href: "https://data-api.binance.vision" },
  { label: "alternative.me", href: "https://alternative.me/crypto/fear-and-greed-index/" },
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
          {i < SOURCES.length - 1 && !compact && <span className="opacity-40">·</span>}
        </span>
      ))}
    </span>
  );
}
