import { rereadWallet } from "../api/client";
import type { State, Wallet } from "../api/types";
import { clockHM, fmtUsd, shortAddr, tokenColor } from "../lib/format";
import AnimatedNumber from "./ui/AnimatedNumber";
import Card from "./ui/Card";
import CheckButton from "./ui/CheckButton";
import CopyButton from "./ui/CopyButton";
import StatusPill from "./ui/StatusPill";
import InfoTip from "./ui/Tooltip";
import { WeightsBody } from "./WeightsDonut";

const ACCENT = "#7C5CFF"; // vlayer purple — brand accent on the "real funds" card
const AMBER = "#f0b90b"; // gold — semantic gas-low warning

/** Human price-source label for the header subtitle. */
function srcLabel(s: string | null): string {
  if (s === "cmc") return "priced via market feed";
  if (s === "chainlink") return "priced via Chainlink";
  if (s === "stable") return "stablecoin · $1";
  return "unpriced";
}

async function walletCheck() {
  const w = await rereadWallet();
  return {
    // Server caches reads ~45s, so don't promise freshness — just truth.
    ok: w.ok,
    detail: w.ok ? `${fmtUsd(w.total_usd)} · block ${w.block ?? "—"}` : w.note ?? "wallet read failed",
  };
}

/** Token amounts span 9 orders of magnitude (0.0036 BNB … 5.06 USDT) — scale the dp. */
function fmtAmount(n: number): string {
  if (n === 0) return "0";
  if (n < 0.001) return n.toExponential(2);
  if (n < 1) return n.toFixed(6);
  if (n < 1000) return n.toFixed(4);
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export default function LiveWalletCard({
  wallet,
  state,
  live = true,
}: {
  wallet: Wallet | null | undefined;
  state: State;
  live?: boolean;
}) {
  // H1: only pulse green "live" when the API is genuinely fresh AND the read succeeded.
  // On the static snapshot fallback (!live) show a muted "snapshot" badge instead of
  // claiming a live read of real funds (which would contradict StatusBar's demo badge).
  const fresh = live && !!wallet?.ok;
  const liveBadge = fresh ? (
    <StatusPill tone="brand" dot pulse srText="live on-chain read">
      LIVE
    </StatusPill>
  ) : (
    <StatusPill tone="neutral" srText="static snapshot">
      SNAPSHOT
    </StatusPill>
  );

  if (!wallet || !wallet.ok) {
    return (
      <Card
        label="Real Funds · On-Chain"
        accent={ACCENT}
        className="h-full"
        right={
          <span className="flex items-center gap-1.5">
            <InfoTip term="realFunds" />
            {liveBadge}
          </span>
        }
      >
        <div className="flex h-full min-h-[9rem] flex-col items-center justify-center gap-1 text-xs text-muted">
          <span>live wallet read unavailable</span>
          {wallet?.note && <span className="text-[10px] opacity-70">{wallet.note}</span>}
        </div>
      </Card>
    );
  }

  const { address, explorer_url, assets, total_usd, priced_source, gas_low, gas_bnb, x402_budget_usdc, block, served_at } =
    wallet;

  return (
    <Card label="Real Funds · On-Chain" accent={ACCENT} className="h-full" right={liveBadge}>
      <div className="space-y-3">
        <div>
          <div className="font-display text-3xl font-bold leading-tight tabular-nums" style={{ color: ACCENT }}>
            {total_usd != null ? <AnimatedNumber value={total_usd} format={(n) => fmtUsd(n)} flash /> : fmtUsd(total_usd)}
          </div>
          <div className="text-[11px] text-muted">actual on-chain balance · {srcLabel(priced_source)}</div>
        </div>

        <div className="space-y-1.5">
          {assets.length === 0 && (
            <div className="text-[10px] text-muted">no assets on-chain</div>
          )}
          {assets.map((a) => (
            <div key={a.symbol} className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: tokenColor(a.symbol) }} />
                <span className="font-medium text-ink">{a.symbol}</span>
                {a.is_gas && (
                  <span
                    className="rounded px-1 text-[9px] uppercase tracking-wide"
                    style={{
                      color: gas_low ? AMBER : "#8a8f9c",
                      border: `1px solid ${gas_low ? `${AMBER}66` : "#3a3d4666"}`,
                    }}
                  >
                    {gas_low ? "gas · low" : "gas"}
                  </span>
                )}
              </span>
              <span className="flex items-center gap-3">
                <span className="font-mono text-sub">{fmtAmount(a.amount)}</span>
                <span className="w-16 text-right font-mono text-sub">
                  {a.usd != null ? fmtUsd(a.usd) : "—"}
                </span>
              </span>
            </div>
          ))}
        </div>

        {gas_low && (
          <div className="rounded-sm border border-[#f0b90b40] bg-[#f0b90b12] px-2 py-1 text-[10px] leading-snug text-amber">
            ⚠ trade-gas buffer thin ({gas_bnb?.toFixed(4)} BNB) — top up before live trading
          </div>
        )}

        <div className="flex items-center justify-between border-t border-edge pt-2 text-[10px] text-muted">
          {explorer_url && address ? (
            <span className="flex items-center gap-1.5">
              <a href={explorer_url} target="_blank" rel="noreferrer" className="font-mono text-cyan hover:underline">
                {shortAddr(address)} ↗
              </a>
              <CopyButton text={address} />
            </span>
          ) : (
            <span />
          )}
          <span className="flex items-center gap-1">
            x402 budget <InfoTip term="x402" /> {x402_budget_usdc != null ? fmtUsd(x402_budget_usdc) : "—"}
          </span>
        </div>
        <div className="flex justify-end">
          <CheckButton label="re-read wallet" run={walletCheck} disabled={!live} />
        </div>
        {(block || served_at) && (
          <div className="-mt-1 text-right text-[9px] text-muted/70">
            {block ? `block ${block.toLocaleString()}` : ""}
            {block && served_at ? " · " : ""}
            {served_at ? `as of ${clockHM(served_at)}` : ""}
          </div>
        )}

        {/* Portfolio weights — the post-rebalance target allocation, folded in next to the real balance. */}
        <div className="border-t border-edge pt-3">
          <div className="mb-2 flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted">
            Portfolio Weights <InfoTip term="deployed" />
          </div>
          <WeightsBody state={state} nav={state.nav ?? 0} />
        </div>
      </div>
    </Card>
  );
}
