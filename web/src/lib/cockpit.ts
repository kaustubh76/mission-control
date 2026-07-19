// The "cockpit" layer's single source of truth: the window-event bus the overlays
// open on, the keyboard maps the KeyboardLayer + Cheatsheet share, the guided-tour
// script, and a tiny helper the keyboard layer uses to stand down while any overlay
// is up. Nav targets reference panel labels (the same strings stamped onto
// `[data-section-label]`), so a key always resolves to a real scroll target via
// `sectionId(label)` — no drift.

/** Fire to open the keyboard-shortcuts cheatsheet (dispatched by `?`, the ⌘K palette,
 * and the header `?` chip). */
export const OPEN_CHEATSHEET_EVENT = "mc:cheatsheet";
/** Fire to start the judge-facing guided tour (dispatched by the ⌘K palette). */
export const START_TOUR_EVENT = "mc:tour";

/** `g`-then-key quick-nav. Curated, collision-free letters → panel labels. */
export interface NavKey {
  key: string;
  label: string;
}
export const NAV_KEYS: NavKey[] = [
  { key: "h", label: "Hero" },
  { key: "e", label: "Equity curve" },
  { key: "n", label: "NAV" },
  { key: "p", label: "P&L" },
  { key: "w", label: "Wallet" },
  { key: "m", label: "Market intelligence" },
  { key: "o", label: "Weights" },
  { key: "c", label: "Market Data Hub" },
  { key: "b", label: "Rebalances" },
  { key: "a", label: "Rationale" },
  { key: "s", label: "Strategy Lab" },
  { key: "d", label: "System diagnostics" },
];

/** Safe single-key power actions. Deliberately NO bare-key kill switch — that stays
 * behind the ⌘K palette + Controls panel so a stray keypress can't halt the agent. */
export type PowerAction = "cheatsheet" | "theme" | "refresh";
export interface PowerKey {
  key: string;
  label: string;
  action: PowerAction;
  /** Needs the live API (guarded with a warn toast when offline). */
  guarded?: boolean;
}
export const POWER_KEYS: PowerKey[] = [
  { key: "?", label: "Keyboard shortcuts", action: "cheatsheet" },
  { key: "t", label: "Toggle theme", action: "theme" },
  { key: "r", label: "Refresh data", action: "refresh" },
];

/** Ordered narration for the guided tour. Filtered at runtime to the panels actually
 * present in the DOM (`[data-section-label]`), so a removed panel just drops its step. */
export interface TourStep {
  label: string;
  narration: string;
}
export const TOUR_STEPS: TourStep[] = [
  { label: "Hero", narration: "The headline: live NAV, how much risk the agent is deploying right now, and its on-chain status." },
  { label: "Equity curve", narration: "Every live rebalance marked to market — the agent's track record at a glance." },
  { label: "P&L", narration: "Cumulative and daily P&L plus win rate. Is the edge real?" },
  { label: "Wallet", narration: "Real funds on-chain — self-custodied and priced live. Proof this isn't only a simulation." },
  { label: "Market intelligence", narration: "Live global market metrics, sentiment and movers the agent reads on every tick." },
  { label: "Weights", narration: "The target allocation the agent is holding after its latest rebalance." },
  { label: "Market Data Hub", narration: "The live free-data stack — Binance candles, alternative.me Fear & Greed, DexScreener and CoinGecko fused into the regime read." },
  { label: "Rebalances", narration: "The audit trail: every tick's decision, swaps and on-chain transactions." },
  { label: "System diagnostics", narration: "Read-only probes proving each subsystem is live and healthy." },
];

/** True while any cockpit overlay (palette / cheatsheet / tour) is mounted — the
 * keyboard layer stands down so its single-key shortcuts never fight an open modal. */
export function overlayOpen(): boolean {
  return !!document.querySelector(".mc-overlay");
}
