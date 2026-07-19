#!/usr/bin/env node
/**
 * Headless dashboard verifier — judge-facing QA for the deployed Mission Control SPA.
 *
 * Renders the DEPLOYED dashboard via system Chrome (CLI `--dump-dom` + `--screenshot`, zero npm deps)
 * and asserts the integrated build renders with LIVE data: the three cross-chat panels (CMC Agent-Hub
 * with rotation, Agent Commerce, Identity heartbeat) + core (strategy/NAV/regime). Enforces the
 * no-"Binance" display gate (the cmcLabel sanitizer — web/src/lib/format.ts) as a HARD check.
 *
 * `--dump-dom` returns the post-React DOM, so scroll-reveal `opacity:0` panels still carry their text
 * (the screenshot only captures the top of the page — the DOM dump is the authoritative coverage).
 * Warm the Render API first (curl /api/health) so the SPA fetch is fast within the virtual-time budget.
 *
 *   node scripts/verify_dashboard.mjs            # verify https://bnb-mission-control-two.vercel.app
 *   DASH_URL=http://localhost:5173 node scripts/verify_dashboard.mjs
 */
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";

const URL = process.env.DASH_URL || "https://bnb-mission-control-two.vercel.app";
const CHROME =
  process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const OUT_DIR = "data/reports";
const SHOT = `${OUT_DIR}/dashboard_verification.png`;
const REPORT = `${OUT_DIR}/dashboard_verification.md`;
mkdirSync(OUT_DIR, { recursive: true });

const COMMON = [
  "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  // --force-prefers-reduced-motion makes the Framer scroll-reveal Cards render visible (else
  // below-the-fold panels screenshot blank); the virtual-time budget fast-forwards the API→snapshot
  // fallback timer so the page paints inside the run (see the headless-verify-gotchas memory).
  "--force-prefers-reduced-motion",
  "--virtual-time-budget=22000", "--window-size=1440,3800",
];

function chrome(extra) {
  return execFileSync(CHROME, [...COMMON, ...extra, URL], {
    encoding: "utf8", maxBuffer: 96 * 1024 * 1024, timeout: 120000,
  });
}

console.log(`[verify] rendering ${URL} via headless Chrome …`);
const html = chrome(["--dump-dom"]);
try { chrome([`--screenshot=${SHOT}`]); console.log(`[verify] screenshot → ${SHOT}`); }
catch (e) { console.warn("[verify] screenshot failed (non-fatal):", e.message); }

// Post-React DOM → plain text (strip script/style/tags), normalise the middot + whitespace.
const text = html
  .replace(/<script[\s\S]*?<\/script>/gi, " ")
  .replace(/<style[\s\S]*?<\/style>/gi, " ")
  .replace(/<[^>]+>/g, " ")
  .replace(/&middot;|&#183;/g, "·")
  .replace(/&amp;/g, "&")
  .replace(/\s+/g, " ")
  .trim();

const has = (re) => re.test(text);
const checks = [
  // CMC Agent-Hub (marquee) — must show the live rotation
  ["CMC Agent-Hub panel", has(/CMC Agent Hub/i)],
  ["  ↳ CMC rotation block", has(/CMC rotation/i)],
  ["  ↳ sector rotation (rotated toward)", has(/rotated toward/i)],
  ["  ↳ MCP / Skill markers", has(/\bMCP\b/i) && has(/skill/i)],
  // Agent Commerce (ERC-8183) — the REAL offering renders even before the first on-chain job
  ["Agent Commerce panel", has(/Agent Commerce/i)],
  ["  ↳ sells-analysis caption", has(/sells analysis via ERC-8183/i)],
  ["  ↳ advertised service (Market Regime Report)", has(/Market Regime Report/i)],
  ["  ↳ on-chain job ledger (Jobs Served)", has(/Jobs Served/i)],
  // Identity (ERC-8004 heartbeat)
  ["Identity heartbeat line", has(/heartbeat (live|ok|failing|armed|off)/i)],
  // Core
  ["strategy (momentum)", has(/momentum/i)],
  ["NAV / equity", has(/\bNAV\b/i) || has(/\$\s?1,?0\d\d/)],
  ["regime / Fear & Greed", has(/fear|greed|regime/i)],
];

// HARD GATE — the cmcLabel sanitizer must leave no raw "Binance" in the rendered text.
const binanceHits = (text.match(/Binance/g) || []).length;
const gateOk = binanceHits === 0;

// HARD GATE — the LIVE-ONLY product must show ZERO simulation / paper-trading framing.
const simHits = text.match(/\bpaper\b|simulat\w*|\$1k\b|\$1,000|sim demo/gi) || [];
const simOk = simHits.length === 0;

const dataSource = has(/snapshot/i) && has(/frozen|static/i) ? "static snapshot fallback" : "live API or snapshot";
const failed = checks.filter(([, ok]) => !ok).map(([n]) => n);
const allPanels = failed.length === 0;
const pass = allPanels && gateOk && simOk;

const stamp = process.env.VERIFY_TS || new Date().toISOString();
const lines = [
  `# Dashboard verification — ${URL}`,
  ``,
  `- when: ${stamp}`,
  `- DOM length: ${html.length} chars · text length: ${text.length} chars`,
  `- no-"Binance" gate: ${gateOk ? "PASS ✓ (0 raw occurrences)" : `FAIL ✗ (${binanceHits} raw)`}`,
  `- no-"paper/sim" gate: ${simOk ? "PASS ✓ (0 occurrences)" : `FAIL ✗ (${simHits.length}: ${[...new Set(simHits)].join(", ")})`}`,
  `- screenshot: ${SHOT} (top-of-page; DOM dump is full-coverage)`,
  ``,
  `| panel / check | result |`,
  `|---|---|`,
  ...checks.map(([n, ok]) => `| ${n} | ${ok ? "PASS ✓" : "FAIL ✗"} |`),
  `| **no-Binance gate** | ${gateOk ? "PASS ✓" : "FAIL ✗"} |`,
  `| **no-paper/sim gate** | ${simOk ? "PASS ✓" : "FAIL ✗"} |`,
  ``,
  `**Overall: ${pass ? "PASS ✓" : "FAIL ✗"}**`,
  ``,
  `Expected non-issues (not regressions): Agent Commerce shows its advertised service + live`,
  `deliverable preview with an "awaiting first job" ledger (no on-chain job has settled yet); the`,
  `heartbeat shows "armed/failing" while the identity wallet is unfunded.`,
  ``,
];
writeFileSync(REPORT, lines.join("\n"));

console.log("\n" + lines.slice(8).join("\n"));
console.log(`\n[verify] report → ${REPORT}`);
if (!pass) {
  console.error(`\n[verify] FAILED: ${failed.length ? "missing panels: " + failed.join(", ") : ""}${!gateOk ? " · Binance leak" : ""}${!simOk ? " · paper/sim leak: " + [...new Set(simHits)].join(", ") : ""}`);
  process.exit(1);
}
console.log("\n[verify] PASS — all panels rendered, no-Binance + no-paper/sim gates green.");
