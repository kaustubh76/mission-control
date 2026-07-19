import type { CommerceBlock, EconomyPnL } from "../api/types";
import { fmtUsd } from "../lib/format";
import Card from "./ui/Card";
import StatusPill, { type Tone } from "./ui/StatusPill";

const UP = "#16c784";
const DOWN = "#ea3943";
const NEU = "#8a8f9c";
const signedUsd = (v: number | null | undefined): string =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${fmtUsd(Math.abs(v))}`;
const clr = (v: number | null | undefined): string => (v == null ? NEU : v > 0 ? UP : v < 0 ? DOWN : NEU);

/**
 * Agent economy — the HEADLINE is the TRADING PnL only (the strategy book's NAV vs the start anchor,
 * net of fees + gas). x402 (data cost, paid from a different wallet) and the self-funded ERC-8183
 * commerce (buyer + provider are both operator wallets → nets to ~−gas, not revenue) are REAL on-chain
 * activity but NOT trading results, so they're shown as separate CONTEXT lines — never summed into PnL.
 */
export default function EconomyPnLCard({
  economy,
  commerce,
}: {
  economy: EconomyPnL | null | undefined;
  commerce?: CommerceBlock | null;
}) {
  if (!economy) {
    return (
      <Card label="Agent economy" accent="#7C5CFF">
        <div className="flex h-20 items-center justify-center text-xs text-muted">economy unavailable</div>
      </Card>
    );
  }
  const pnl = economy.trading_pnl_usd; // HEADLINE = trading PnL only
  const pnlTone: Tone = pnl == null ? "neutral" : pnl >= 0 ? "up" : "down";
  const pendingSettle = commerce?.jobs_pending_settle ?? 0;

  return (
    <Card
      label="Agent economy"
      accent="#7C5CFF"
      right={
        <StatusPill tone={pnlTone} dot srText={`trading PnL ${signedUsd(pnl)}`}>
          PnL {signedUsd(pnl)}
        </StatusPill>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="font-display text-2xl font-bold tabular-nums" style={{ color: clr(pnl) }}>
            {signedUsd(pnl)}
          </span>
          <span className="text-[10.5px] text-muted">trading PnL · LIVE book vs start anchor (net of fees + gas)</span>
        </div>

        {/* Context — REAL on-chain activity, but NOT trading results → never summed into the PnL above. */}
        <div className="space-y-1.5 border-t border-edge pt-2 text-[11px]">
          <div className="text-[9px] uppercase tracking-wide text-muted">context · not counted in PnL</div>
          <Flow
            label="x402 data cost"
            detail="x402 paid data on Base · operating expense (identity wallet)"
            value={economy.x402_spent_usd != null ? `−${fmtUsd(economy.x402_spent_usd)}` : "—"}
            color={NEU}
          />
          <Flow
            label="Commerce · self-funded"
            detail={`ERC-8183 proof · ${(economy.commerce_revenue_u ?? 0).toFixed(2)} U${
              pendingSettle > 0 ? ` · ${pendingSettle} pending settle` : ""
            } · ~−gas, not revenue`}
            value={`${(economy.commerce_revenue_u ?? 0).toFixed(2)} U`}
            color={NEU}
          />
        </div>

        <div className="text-[10px] leading-relaxed text-muted">
          {economy.commerce_note}
          {economy.x402_note ? (
            <>
              <br />
              {economy.x402_note}
            </>
          ) : null}
          {economy.gas_note ? (
            <>
              <br />
              {economy.gas_note}
            </>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function Flow({
  label,
  detail,
  value,
  color,
}: {
  label: string;
  detail: string;
  value: string;
  color: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="flex min-w-0 flex-col">
        <span className="text-sub">{label}</span>
        <span className="truncate text-[9px] text-muted">{detail}</span>
      </span>
      <span className="shrink-0 font-mono tabular-nums" style={{ color }}>
        {value}
      </span>
    </div>
  );
}
