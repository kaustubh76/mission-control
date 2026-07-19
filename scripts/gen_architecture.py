#!/usr/bin/env python3
"""Generate docs/architecture.{excalidraw,svg,png} — the DETAILED, full-system architecture of the
BNB MOMENTUM trading agent.

Layout = five stacked, top→down LAYER PLANES (read the bands in order):
  ①  DATA INGEST    — CMC-native pipeline (WebSocket → bars/snapshots → store → unified signals)
  ②  DECIDE         — pluggable strategy registry + regime-adaptive cap + auto-selector + campaign gate
  ③  EXECUTE        — one TWAK rebalance tick(), every guard fails safe (skip / halt, never crash)
  ④  AGENTIC ECONOMY— the three protocols on ONE identity:  x402 + ERC-8004 + ERC-8183
  ⑤  PERSIST & OBSERVE — atomic journal/state → snapshot ingest → Mission Control (two-tier scaling)

Within a plane cards flow left→right; between planes a bold connector flows down. Zero ICT, zero CEX
on the contest path (CMC_ONLY firewall).

Anti-staleness: the x402 receipt count/total and the ERC-8183 job ids are DERIVED at build time from
data/x402/receipts.json and data/journal/commerce_jobs.jsonl, so the diagram can never drift from the
journals. Before writing, the script self-asserts that every required layer token is present.

Emits a modern Excalidraw schema PLUS a deterministic 1:1 SVG mirror (docs/architecture.svg) and, if
cairosvg is present, a PNG — viewable in any browser / GitHub, immune to render-cache quirks.
"""
import json
import math
import os

EL = []
_n = [0]
CIRCLED = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤", 6: "⑥", 7: "⑦", 8: "⑧", 9: "⑨",
           10: "⑩", 11: "⑪", 12: "⑫", 13: "⑬", 14: "⑭", 15: "⑮", 16: "⑯", 17: "⑰"}
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _id():
    _n[0] += 1
    return _n[0]


# ---- grouping (Excalidraw groupIds: innermost first, outermost last) ----
_g = [0]
_PLANE_G = [None]
_PLANE_START = [None]


def _gid():
    _g[0] += 1
    return f"g{_g[0]}"


def _stamp_inner(start):
    """Group the EL slice [start:] (one card/pill/gate) so it moves as a unit."""
    gid = _gid()
    for e in EL[start:]:
        e["groupIds"] = [gid] + e.get("groupIds", [])


def _close_prev_plane():
    """Append the current plane's group as the OUTERMOST group on every element drawn in it."""
    if _PLANE_G[0] is None or _PLANE_START[0] is None:
        return
    gid = _PLANE_G[0]
    for e in EL[_PLANE_START[0]:]:
        if gid not in e.get("groupIds", []):
            e["groupIds"] = e.get("groupIds", []) + [gid]


# Width estimate, per font family, SAFELY >= Excalidraw's true metric (mono≈0.60, sans≈0.50 per
# pt/char) so a non-autoResize box never wraps when the .excalidraw is opened in the app.
def _twidth(s, size, family):
    per = 0.62 if family == 3 else 0.56
    return max((len(ln) for ln in s.split("\n")), default=1) * size * per + 8


def _el(**kw):
    i = _id()
    d = dict(id=f"el{i}", angle=0, strokeColor="#1e1e1e", backgroundColor="transparent",
             fillStyle="solid", strokeWidth=2, strokeStyle="solid", roughness=0, opacity=100,
             groupIds=[], frameId=None, roundness=None, seed=10007 + i * 131, version=2,
             versionNonce=20011 + i * 977, isDeleted=False, boundElements=[],
             updated=1700000000000, link=None, locked=False)
    d.update(kw)
    return d


def rect(x, y, w, h, stroke, fill, sw=2, rounded=True, dashed=False):
    EL.append(_el(type="rectangle", x=x, y=y, width=w, height=h, strokeColor=stroke,
                  backgroundColor=fill, strokeWidth=sw,
                  strokeStyle="dashed" if dashed else "solid",
                  roundness={"type": 3} if rounded else None))


def diamond(x, y, w, h, stroke, fill, sw=2):
    EL.append(_el(type="diamond", x=x, y=y, width=w, height=h, strokeColor=stroke,
                  backgroundColor=fill, strokeWidth=sw, roundness={"type": 2}))


def ellipse(x, y, w, h, stroke, fill, sw=2):
    EL.append(_el(type="ellipse", x=x, y=y, width=w, height=h, strokeColor=stroke,
                  backgroundColor=fill, strokeWidth=sw))


# autoResize=False + an accurate box → Excalidraw renders at our exact coords (no on-load reflow), so
# the app view matches the SVG mirror 1:1. emit_svg uses x (left) / x+width/2=cx (centre) — unchanged.
def text(x, y, s, size=15, color="#1e1e1e", family=2):
    EL.append(_el(type="text", x=x, y=y, width=_twidth(s, size, family),
                  height=len(s.split("\n")) * size * 1.25, strokeColor=color, strokeWidth=1,
                  fontSize=size, fontFamily=family, text=s, originalText=s, textAlign="left",
                  verticalAlign="top", containerId=None, lineHeight=1.25, autoResize=False))


def tcenter(cx, cy, s, size=14, color="#1e1e1e", family=2):
    w = _twidth(s, size, family)
    h = len(s.split("\n")) * size * 1.25
    EL.append(_el(type="text", x=cx - w / 2, y=cy - h / 2, width=w, height=h, strokeColor=color,
                  strokeWidth=1, fontSize=size, fontFamily=family, text=s, originalText=s,
                  textAlign="center", verticalAlign="top", containerId=None, lineHeight=1.25,
                  autoResize=False))


def arrow(x1, y1, x2, y2, color="#495057", sw=2.5, label=None, dashed=False):
    EL.append(_el(type="arrow", x=x1, y=y1, width=x2 - x1, height=y2 - y1, strokeColor=color,
                  strokeWidth=sw, strokeStyle="dashed" if dashed else "solid",
                  points=[[0.0, 0.0], [float(x2 - x1), float(y2 - y1)]],
                  lastCommittedPoint=None, startBinding=None, endBinding=None,
                  startArrowhead=None, endArrowhead="arrow", roundness={"type": 2}))
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if abs(x2 - x1) < abs(y2 - y1):      # vertical → label to the side
            text(mx + 8, my - 9, label, 11, color)
        else:                                 # horizontal → label above
            tcenter(mx, my - 11, label, 11, color)


# ---------------- colours (stroke, fill, faint-band) ----------------
DATA = ("#1971c2", "#e7f5ff", "#f3f9ff")
STRAT = ("#2b8a3e", "#ebfbee", "#f4fdf6")
EXEC = ("#3b5bdb", "#edf2ff", "#f5f7ff")
SAFE = ("#c92a2a", "#ffe3e3", "#fff5f5")
IDENT = ("#6741d9", "#f3f0ff", "#f8f6ff")
VLAYER = ("#9c36b5", "#f8f0fc", "#fdf4ff")   # grape — vlayer verifiable data-provenance (zkTLS)
OBS = ("#0c8599", "#e3fafc", "#f2fcfd")
PROC = ("#495057", "#f8f9fa", "#fbfcfd")
SKIPC = ("#868e96", "#f1f3f5", "#fafafa")

# ---------------- grid ----------------
PLANE_X, PLANE_W = 50, 2620
CARD_W, CARD_H = 432, 100


def colx(i):
    return 110 + i * 486            # 110 · 596 · 1082 · 1568 · 2054 (right edge 2486)


def plane(y, h, label, role):
    _close_prev_plane()                 # close the previous layer's group
    _PLANE_G[0] = _gid()                # open this layer's group
    _PLANE_START[0] = len(EL)
    rect(PLANE_X, y, PLANE_W, h, role[0], role[2], sw=1.5)
    text(PLANE_X + 18, y + 11, label, 16, role[0])


def acard(x, y, title, subs, role, w=CARD_W, h=CARD_H, num=None, dashed=False, emph=False):
    start = len(EL)
    st, fi = role[0], role[1]
    rect(x, y, w, h, st, fi, sw=3 if emph else 2, dashed=dashed)
    t = f"{CIRCLED[num]}  {title}" if num else title
    text(x + 15, y + 9, t, 15 if emph else 14, "#1e1e1e")
    yy = y + 33
    for s in subs:
        text(x + 15, yy, s, 11, st, family=3)
        yy += 15.5
    _stamp_inner(start)
    return (x, y, w, h)


def agate(cx, cy, label, caption, role=SAFE, w=300, h=96):
    start = len(EL)
    diamond(cx - w / 2, cy - h / 2, w, h, role[0], role[1], sw=2)
    tcenter(cx, cy - 8, label, 13, "#1e1e1e")
    if caption:
        tcenter(cx, cy + 12, caption, 9.5, role[0])
    _stamp_inner(start)
    return (cx - w / 2, cy - h / 2, w, h)


def pill(x, y, w, h, title, sub, role, fill=None):
    start = len(EL)
    st = role[0]
    rect(x, y, w, h, st, fill or role[1], sw=2.5)
    text(x + 13, y + 9, title, 12, "#1e1e1e")
    if sub:
        text(x + 13, y + 28, sub, 9, st, family=3)
    _stamp_inner(start)
    return (x, y, w, h)


def harrow(a, b, label=None, color="#495057", dashed=False):
    (x, y, w, h) = a
    (x2, y2, w2, h2) = b
    arrow(x + w, y + h / 2, x2, y2 + h2 / 2, color, sw=2.5, label=label, dashed=dashed)


def vdown(x, y1, y2, label, color="#343a40"):
    arrow(x, y1, x, y2, color, sw=3.5, label=label)


# ---------------- derive live proof from the journals (anti-staleness) ----------------
def _proof():
    rx = {"n": None, "usd": None, "dex": None, "quotes": None}
    try:
        with open(os.path.join(BASE, "data/x402/receipts.json")) as f:
            d = json.load(f)
        s = [r for r in d if r.get("status") == "settled"]
        rx["n"] = len(s)
        rx["usd"] = sum(int(r.get("value", 0)) for r in s) / 1e6
        rx["dex"] = sum(1 for r in s if "dex/search" in (r.get("endpoint") or ""))
        rx["quotes"] = sum(1 for r in s if "quotes" in (r.get("endpoint") or ""))
    except Exception:
        pass
    jobs = {"ids": [], "n": None}
    try:
        ids = set()
        with open(os.path.join(BASE, "data/journal/commerce_jobs.jsonl")) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                j = json.loads(line)
                if j.get("job_id") is not None:
                    ids.add(int(j["job_id"]))
        jobs["ids"] = sorted(ids)
        jobs["n"] = len(ids)
    except Exception:
        pass
    return rx, jobs


RX, JOBS = _proof()
if RX["n"] is not None:
    X402_PROOF = f"{RX['dex']}× dex/search · {RX['quotes']}× quotes · {RX['n']} settled · ${RX['usd']:.2f}"
else:
    X402_PROOF = "receipts: data/x402/receipts.json"
if JOBS["n"]:
    JOB_PROOF = f"{JOBS['n']} jobs on BSC mainnet: " + " · ".join(str(i) for i in JOBS["ids"])
else:
    JOB_PROOF = "jobs: data/journal/commerce_jobs.jsonl"

# ============================================================ HEADER
text(56, 30, "BNB Regime-Adaptive Trading Agent — full system architecture", 30)
text(56, 74, "Five layers · one allocator tick · the agentic economy:  "
     "x402 (buy data)  ·  ERC-8004 (on-chain identity)  ·  ERC-8183 (sell analysis)  ·  vlayer Web Proofs (prove the data)", 15, "#1971c2")
text(56, 100, "Read the bands TOP→DOWN:  ① CMC-native data  →  ② decide (pluggable strategies)  →  "
     "③ execute (TWAK, sole signer)  →  ④ agentic economy (3 protocols + vlayer proof)  →  ⑤ persist & observe."
     "    Every guard fails safe — skip or halt, never crash.", 12, "#495057")
# legend
ly = 138
leg = [("process", PROC), ("◆ decision", SAFE), ("CMC data", DATA), ("strategy", STRAT),
       ("TWAK exec", EXEC), ("identity / 3 protocols", IDENT), ("vlayer proof · roadmap", VLAYER),
       ("observability", OBS), ("skip/halt", SKIPC)]
lx = 56
for lab, role in leg:
    rect(lx, ly, 15, 15, role[0], role[1], sw=2)
    text(lx + 21, ly - 1, lab, 11, "#343a40")
    lx += 26 + len(lab) * 7.1
text(56, ly + 24, "return codes:   0 = ok / no-op   ·   1 = drawdown HALT (flattened)   ·   2 = skip this tick (guard tripped)",
     11, "#868e96", family=3)
text(56, ly + 44, "on-chain anchors:   identity 0xEb7b…9655  ·  agentId 133085  ·  registry 0x8004…a432  ·  "
     "trading wallet 0xE8A3…6215  ·  USDC@Base 0x8335…2913", 11, "#6741d9", family=3)
text(56, ly + 64, "card styles:   solid colour = LIVE on the contest path   ·   "
     "grey dashed = ROADMAP (built · human-gated · not yet auto-live)", 11, "#868e96", family=3)

CX = PLANE_X + PLANE_W / 2     # 1360 — centre line for inter-plane connectors

# ============================================================ ① DATA INGEST — CMC-native
P1 = 250
plane(P1, 350, "①  DATA INGEST — CMC-native pipeline    (pillar ①: the eyes · CMC_ONLY firewall → CoinMarketCap-only)", DATA)
r1 = P1 + 44
d0 = acard(colx(0), r1, "CMC WebSocket feed", [
    "market@crypto_latest_price (price·mc·cs·vol·pXXd)",
    "onchain@ token_metric · holders · liquidity · whale",
    "scripts/cmc_stream.py · auto-reconnect daemon"], DATA)
d1 = acard(colx(1), r1, "stream → bars + snapshots", [
    "BarBuilder → 4h parquet (UTC grid, idempotent)",
    "QuoteSnapshotWriter · on-chain harvester",
    "heartbeat ts → watchdog"], DATA)
d2 = acard(colx(2), r1, "cmc_stream_store.py", [
    "zero-network · never-raise · staleness-gated",
    "quote_snapshot() · onchain_* readers"], DATA)
d3 = acard(colx(3), r1, "market_signals.py", [
    "unified per-token buffet:",
    "pct_24h/7d/30d · flow_ratio · liquidity_usd",
    "whale_net · holders top10/top100"], DATA)
harrow(d0, d1)
harrow(d1, d2)
harrow(d2, d3)
r2 = P1 + 198
m0 = acard(colx(0), r2, "CMC Agent Hub · MCP", [
    "8 tools → composed market-overview skill",
    "per-token TA · F&G · BTC-dom · mktcap · derivatives",
    "macro-event de-risk · news brake → risk budget ∈ [0,1]"], DATA)
m1 = acard(colx(1), r2, "CMC Pro REST + 4h candles", [
    "price_fn · fear_greed · daily backfill",
    "cold-start seed → cmc_seed_vol_floor"], DATA)
m2 = acard(colx(2), r2, "x402 — paid data (mechanism)", [
    "402 challenge → sign EIP-3009 USDC@Base → resend",
    X402_PROOF, "caps: $0.01/call · $1/session  (full role → ④)"], DATA)
pill(colx(3), r2 + 6, CARD_W, 78, "CMC_ONLY  firewall", None, SAFE)
text(colx(3) + 13, r2 + 36, "CMC_ONLY=true → live ticks use CoinMarketCap only.\n"
     "any non-CMC candle source is refused (fail-safe),\nso the contest arm is CMC-native by construction.",
     10, SAFE[0], family=3)

# ============================================================ ② DECIDE — strategies
P2 = 650
plane(P2, 330, "②  DECIDE — regime-adaptive strategy engine    (long-only spot · live arm: mean-reversion · NO leverage)", STRAT)
s1 = P2 + 44
e0 = acard(colx(0), s1, "resolve strategy", [
    "LIVE = settings pin (.env STRATEGY_NAME)",
    "registry.get(name) → PortfolioStrategy"], STRAT)
e1 = acard(colx(1), s1, "live arm — mean-reversion", [
    "buy oversold (>1σ below mean) · inverse-vol · capped",
    "survival-passed: 13.2% DD · ~26 trades/wk · DQ-safe",
    "incumbent/fallback: momentum_adaptive · 20 arms"], STRAT)
e2 = acard(colx(2), s1, "regime score → adaptive cap", [
    "breadth + trend + vol + F&G + CMC TA/macro",
    "cap ∈ [0.40, 0.85] · remainder = USDT"], STRAT)
e3 = acard(colx(3), s1, "target = weights × cap", [
    "inverse-vol sizing · abs-return filter",
    "(flag) CMC 7-day rel-strength tilt"], STRAT)
harrow(e0, e1)
harrow(e1, e2)
harrow(e2, e3)
s2 = P2 + 198
acard(colx(0), s2, "ROADMAP · auto-selector (forward-gated switching)", [
    "risk-adj forward score · anti-chasing hysteresis (2-eval)",
    "SIM auto-drives · LIVE recommend-only (auto-apply OFF)",
    "switching engine built — promotes only on human sign-off"], SKIPC, w=CARD_W + 200, dashed=True)
acard(colx(2) + 86, s2, "ROADMAP · multi-strategy campaign (5-stage gate)", [
    "registered → backtest-survival → forward-started → eligible",
    "→ operator sign-off · strategy_gates.json · risk-first board",
    "validation harness — proves a challenger before promotion"], SKIPC, w=CARD_W + 200, dashed=True)

# ============================================================ ③ EXECUTE — TWAK tick()
P3 = 1010
plane(P3, 620, "③  EXECUTE — TWAK rebalance, one tick()    (pillar ②: the hands · TWAK = sole signer · gasless)", EXEC)
xa = P3 + 46
t0 = acard(colx(0), xa, "run_allocator.py  tick(--mode)", [
    "sim ↔ live · --dd-watch --resume --dd-cap",
    "--ensure-daily-floor · --unlock-profit"], PROC, num=1)
t1 = acard(colx(1), xa, "load_state  (atomic)", [
    "HWM · cumulative_swaps · halted · balances",
    ".tmp+os.replace · corrupt → safe defaults"], PROC, num=2)
t2 = acard(colx(2), xa, "FAIL-SAFE GUARDS", [
    "lock (flock) · data ≥200 bars/≥3 tok · fresh ≤12h",
    "LIVE preflight (creds·wallet·enable) · px>0 & nav>0",
    "any fail → SKIP (return 2) · never crash"], SAFE, num=3)
t3 = acard(colx(3), xa, "NAV · HWM · drawdown", [
    "nav = USDT + Σ holdings·px",
    "HWM=max(nav,HWM) · dd=(HWM−nav)/HWM"], SAFE, num=4)
gdd = agate(colx(4) + CARD_W / 2, xa + CARD_H / 2, "dd > cap?", "0.10 campaign · 0.30 DQ", SAFE)
harrow(t0, t1)
harrow(t1, t2)
harrow(t2, t3)
arrow(t3[0] + t3[2], xa + CARD_H / 2, colx(4) + CARD_W / 2 - 150, xa + CARD_H / 2, SAFE[0], sw=2.5)
# dd halt terminals under the gate
hf = pill(colx(4) + 20, xa + 132, CARD_W - 40, 50, "EMERGENCY FLATTEN", "sell→USDT · 3× retry", SAFE)
hh = pill(colx(4) + 60, xa + 210, CARD_W - 120, 46, "DD_HALT  (return 1)", "halt=True · book flat", SAFE, fill="#ffc9c9")
arrow(colx(4) + CARD_W / 2, xa + CARD_H, colx(4) + CARD_W / 2, xa + 132, SAFE[0], sw=2.5, label="yes")
arrow(colx(4) + CARD_W / 2, xa + 182, colx(4) + CARD_W / 2, xa + 210, SAFE[0], sw=2.5)

xb = P3 + 300
b0 = acard(colx(0), xb, "rebalance diff", [
    "TwakSpotBroker.rebalance(target, prices)",
    "skip moves <2% NAV · min-notional $1"], EXEC, num=5)
b1 = acard(colx(1), xb, "SELL overweight → USDT", [
    "frees quote first · qty=min(bal, −Δ/px)"], EXEC, num=6)
b2 = acard(colx(2), xb, "BUY underweight ← USDT", [
    "spend = min(Δ, USDT balance)"], EXEC, num=7)
b3 = acard(colx(3), xb, "twak swap  (CLI)", [
    "--chain bsc · slippage cap · retry/backoff",
    "GASLESS sponsorship · TWAK = sole signer"], EXEC, num=8)
gsw = agate(colx(4) + CARD_W / 2, xb + CARD_H / 2, "swap ok?", "amount_out>0 AND tx", EXEC)
# off-page connector Ⓐ : dd-gate "no" (far right, row A) → rebalance entry (far left, row B) — no diagonal
_gx = colx(4) + CARD_W / 2
arrow(_gx, xa + CARD_H, _gx, xa + CARD_H + 18, EXEC[0], sw=2.5)
ellipse(_gx - 17, xa + CARD_H + 18, 34, 34, EXEC[0], "#dbe4ff", sw=2.5)
tcenter(_gx, xa + CARD_H + 35, "Ⓐ", 16, EXEC[0])
text(_gx - 252, xa + CARD_H + 26, "no / within cap   →   continue at Ⓐ", 11, EXEC[0])
_bx = colx(0) + CARD_W / 2
ellipse(_bx - 17, xb - 54, 34, 34, EXEC[0], "#dbe4ff", sw=2.5)
tcenter(_bx, xb - 37, "Ⓐ", 16, EXEC[0])
arrow(_bx, xb - 20, _bx, xb, EXEC[0], sw=2.5)
harrow(b0, b1)
harrow(b1, b2)
harrow(b2, b3)
arrow(b3[0] + b3[2], xb + CARD_H / 2, colx(4) + CARD_W / 2 - 150, xb + CARD_H / 2, EXEC[0], sw=2.5)
pill(colx(4) + 20, xb + 132, CARD_W - 40, 46, "failed_swaps[]", "journaled · tick continues", SKIPC)
arrow(colx(4) + CARD_W / 2, xb + CARD_H, colx(4) + CARD_W / 2, xb + 132, SKIPC[0], sw=2.5, label="no")

xc = P3 + 470
acard(colx(0), xc, "auto_trader.sh — supervised daemon", [
    "1 fresh run_allocator per cycle (hourly)",
    "kill-switch · then publish_snapshot.sh"], OBS, h=92, dashed=True)
acard(colx(1), xc, "dd-watch — fast loop (~15 min)", [
    "same flock · read-only HWM · flatten-only",
    "intraday DD_HALT + profit-lock · no opens"], SAFE, h=92, dashed=True)
acard(colx(2), xc, "trade-floor auto-ensure", [
    "≥7 trades/wk + ≥1/day (contest window)",
    "rotation round-trips ~0 NAV · FLOOR_NUDGE"], STRAT, h=92, dashed=True)
acard(colx(3), xc, "profit-lock ratchet (campaign)", [
    "arm +5% · trail 3% · bank +10%",
    "profit_locked ≠ halted (--unlock-profit)"], STRAT, h=92, dashed=True)

# ============================================================ ④ AGENTIC ECONOMY — 3 protocols + vlayer
P4 = 1670
P4_H = 470                          # taller than the other bands: row 1 = protocols, row 2 = vlayer proof
plane(P4, P4_H, "④  AGENTIC ECONOMY — protocols on ONE identity    (x402  +  ERC-8004  +  ERC-8183)   +   vlayer trust layer  (verifiable data-provenance · roadmap)", IDENT)

# ---- row 1: the three LIVE protocols on one identity ----
ay = P4 + 62
ic = acard(colx(2) + 28, ay, "ERC-8004  —  on-chain identity", [
    "agentId 133085 · registry 0x8004…a432",
    "owner / signer 0xEb7b…9655",
    "per-tick gasless heartbeat: ts + NAV + rationale",
    "(NodeReal MegaFuel paymaster · best-effort)"], IDENT, w=CARD_W, h=118, emph=True)
xc4 = acard(colx(0), ay, "x402  —  CONSUMER (buys data)", [
    "pays CMC per request · USDC@Base (eip155:8453)",
    X402_PROOF,
    "payTo 0x3C5f…3eeA · 402 → EIP-3009 → resend",
    "agent's data COST (excluded from PnL)"], DATA, w=CARD_W, h=118)
e8 = acard(colx(4), ay, "ERC-8183  —  PROVIDER (sells analysis)", [
    "sells Market Regime Report to other agents",
    "create_job → fund → submit(IPFS) → settle",
    "optimistic 7-day dispute window (NotDecided)",
    JOB_PROOF], IDENT, w=CARD_W, h=118)
buyer = acard(colx(4) + 40, ay + 130, "peer agent — BUYER", [
    "0x9e4A…74d6 · distinct wallet (true A2A)"], OBS, w=CARD_W - 80, h=48)
arrow(xc4[0] + xc4[2], ay + 59, ic[0], ay + 59, DATA[0], sw=3, label="pays from identity wallet")
arrow(ic[0] + ic[2], ay + 59, e8[0], ay + 59, IDENT[0], sw=3, label="is the provider")
arrow(buyer[0] + buyer[2] / 2, ay + 130, e8[0] + e8[2] / 2, ay + 118, OBS[0], sw=2.5, label="create_job + fund")

# ---- row 2: vlayer verifiable data-provenance — the TRUST layer (ROADMAP · dashed) ----
text(colx(0), P4 + 252, "ROADMAP · vlayer Web Proofs (zkTLS) — prove the sold report's CMC provenance ON-CHAIN, without revealing the API key.  "
     "Scaffolded: RegimeProver/Verifier + prove.ts + provenance.py.  First testnet attestation = milestone M1.", 11, VLAYER[0], family=3)
vy = P4 + 280
v0 = acard(colx(0), vy, "CMC F&G endpoint  (the proven input)", [
    "GET /v3/fear-and-greed/latest · data.value 0–100",
    "integer payload → robust jsonGetInt (no float parse)",
    "API key stays INSIDE the TLS session"], VLAYER, h=118, dashed=True)
v1 = acard(colx(1), vy, "vlayer Notary → Web Proof (zkTLS)", [
    "notarized TLS transcript + notary signature",
    "proves bytes came from pro-api.coinmarketcap.com",
    "vlayer/vlayer/prove.ts (server-side · keyed)"], VLAYER, h=118, dashed=True)
v2 = acard(colx(2), vy, "RegimeProver.main  (Prover)", [
    "web = webProof.verify(DATA_URL)",
    "fearGreed = web.jsonGetInt(\"data.value\")",
    "commits (agent · fearGreed · reportHash)"], VLAYER, h=118, dashed=True)
v3 = acard(colx(3), vy, "RegimeVerifier  (onlyVerified)", [
    "attestation tx → Optimism Sepolia  (M1)",
    "latest[agent] · byReport[reportHash]",
    "emit RegimeProven(agent, fg, cls, hash)"], VLAYER, h=118, dashed=True)
v4 = acard(colx(4), vy, "provenance_proof on the SOLD report", [
    "provenance.py reads latestOf (key-free)",
    "{chain · verifier · tx · fear_greed · proven_at}",
    "\"trust me\" → \"verify it\" — buyer-checkable"], VLAYER, h=118, dashed=True)
harrow(v0, v1)
harrow(v1, v2)
harrow(v2, v3)
harrow(v3, v4)
# the CMC data x402 pays for is exactly what vlayer proves (col 0, row 1 → row 2)
arrow(colx(0) + CARD_W / 2, ay + 118, colx(0) + CARD_W / 2, vy, VLAYER[0], sw=2.5, dashed=True,
      label="the CMC data the agent trusts — now zk-proven")
# the proof rides UP into the sold ERC-8183 deliverable the buyer purchases (col 4, row 2 → buyer)
arrow(colx(4) + CARD_W / 2, vy, buyer[0] + buyer[2] / 2, buyer[1] + buyer[3], VLAYER[0], sw=2.5, dashed=True,
      label="binds the sold report → buyer verifies on-chain")
tcenter(CX, P4 + 438, "one agent, two wallets — BUYS data (x402), SELLS analysis (ERC-8183), anchored by ERC-8004 — and "
        "vlayer Web Proofs make the sold report's CMC provenance cryptographically buyer-verifiable.", 12, VLAYER[0])

# ============================================================ ⑤ PERSIST & OBSERVE
P5 = 2180                            # shifted down by 90 for the taller band ④ (vlayer row)
plane(P5, 350, "⑤  PERSIST & OBSERVE — Mission Control    (zero-secret deploy · two-tier scalability)", OBS)
q1 = P5 + 44
p0 = acard(colx(0), q1, "journal + state  (atomic)", [
    "REBALANCE · DD_HALT · PROFIT_LOCK · FLOOR_NUDGE",
    ".tmp+os.replace · append-only JSONL"], IDENT)
p1 = acard(colx(1), q1, "publish_snapshot.sh → ingest", [
    "POST /api/ingest/snapshot (token-gated)",
    "constant-time HMAC · atomic write"], PROC)
p2 = acard(colx(2), q1, "Mission Control API (Render)", [
    "FastAPI read-only /api/snapshot · /api/agent-hub",
    "ETag/304 · journal→cards · NO signing keys"], OBS)
p3 = acard(colx(3), q1, "React SPA (Vercel)", [
    "EquityCurve · RegimeDial · WeightsDonut",
    "RebalanceTable · RationaleTicker · CmcAgentHubPanel"], OBS)
harrow(p0, p1)
harrow(p1, p2)
harrow(p2, p3)
q2 = P5 + 198
acard(colx(0), q2, "two-tier scalability", [
    "READ tier: edge proxy · ETag · micro-cache (horizontal)",
    "WRITE tier: single-writer · flock/Redis lease (never LB'd)"], PROC, w=CARD_W + 200, dashed=True)
acard(colx(2) + 86, q2, "honest PnL = trading-only", [
    "headline = NAV − anchor (trading only)",
    "commerce (self-funded) + x402 (data cost) excluded, shown separately"],
    SAFE, w=CARD_W + 200, dashed=True)

# ============================================================ ⑥ WIRINGS — text reference
# A rigorous, plain-language map of EVERY connection, so a reader is clear on the wiring just by
# reading: each Wn names the exact card titles above and what flows between them. Neutral band, no
# inbound flow arrow (it is a legend, not a stage).
def wgroup(x, y, header, hcolor, lines):
    text(x, y, header, 12.5, hcolor)
    text(x, y + 19, "\n".join(lines), 11, "#343a40", family=3)
    return y + 19 + int(round(len(lines) * 11 * 1.25)) + 18


P6 = 2570                            # shifted down by 90 (see P5)
plane(P6, 330, "⑥  WIRINGS — every connection, in words    (read together with the arrows above)", PROC)
WY = P6 + 46
# --- column A : arrow grammar + data plane ---
_ya = wgroup(90, WY, "ARROW LEGEND", PROC[0], [
    "SOLID arrow  →   primary flow (down the stack · left→right within a plane)",
    "DASHED arrow      side feed / governs / risk-budget",
    "Ⓐ = off-page 'continue here'    ·    diamond = decision / guard",
    "return codes:   0 = ok   ·   1 = drawdown HALT   ·   2 = skip tick"])
wgroup(90, _ya, "① DATA INGEST   →   ② DECIDE", DATA[0], [
    "W1  CMC WebSocket → cmc_stream.py : live crypto_latest_price + onchain@* (reconnect)",
    "W2  cmc_stream.py → disk : 4h parquet bars + atomic snapshots + heartbeat ts",
    "W3  cmc_stream_store → market_signals : zero-network reads → one per-token buffet",
    "W4  CMC Agent Hub (MCP · 8 tools) → regime : composed skill → risk budget ∈ [0,1]",
    "W5  x402 → CMC (pay) : per-request USDC@Base unlocks dex/search + quotes",
    "①→②   signals + risk budget feed the regime score"])
# --- column B : decide + execute ---
_yb = wgroup(970, WY, "② DECIDE", STRAT[0], [
    "W6  resolve strategy → registry.get(name) : LIVE pins mean-reversion (.env STRATEGY_NAME)",
    "W7  auto-selector → registry : ROADMAP · recommend-only (LIVE never auto-switches)",
    "W8  regime score → adaptive cap [0.40, 0.85] → target = weights × cap",
    "②→③   target weights enter the tick"])
wgroup(970, _yb, "③ EXECUTE", EXEC[0], [
    "W9   guards (lock·data·fresh·preflight·px/nav) : any fail → SKIP (2)",
    "W10  NAV vs HWM → dd>cap? gate : yes → EMERGENCY FLATTEN → DD_HALT (1)",
    "W11  target → TwakSpotBroker → SELL overweight → BUY underweight → twak swap (gasless)",
    "W12  swap ok? gate : no → failed_swaps[] journaled, tick continues",
    "W13  auto_trader.sh → run_allocator (hourly) → publish_snapshot · dd-watch shares the flock"])
# --- column C : economy + persist ---
_yc = wgroup(1850, WY, "④ AGENTIC ECONOMY    (one identity 0xEb7b…9655)", IDENT[0], [
    "③→④   every tick fires the heartbeat (+ commerce on demand)",
    "W14  x402 CONSUMER → pays CMC from the identity wallet (data cost · excluded from PnL)",
    "W15  ERC-8004 → per-tick gasless heartbeat : ts + NAV + rationale (MegaFuel)",
    "W16  ERC-8183 PROVIDER → identity signs create_job→fund→submit(IPFS)→settle ;",
    "         a distinct peer agent (buyer) funds the job — true agent-to-agent",
    "W16b vlayer (ROADMAP) → Web Proof (zkTLS) → RegimeProver → RegimeVerifier :",
    "         on-chain CMC-provenance attestation (M1 · Optimism Sepolia), API key never revealed",
    "W16c provenance.py reads latestOf(agent) (key-free) → the sold report carries a",
    "         buyer-checkable provenance_proof (\"trust me\" → \"verify it\")"])
wgroup(1850, _yc, "⑤ PERSIST & OBSERVE", OBS[0], [
    "W17  tick → journal+state (atomic) → publish_snapshot → POST /api/ingest (HMAC)",
    "W18  API (Render, read-only) → React SPA (Vercel) :",
    "         read tier cached/ETag (horizontal) · write tier single-writer lease",
    "④→⑤   journal + heartbeat outputs flow to persistence / observability"])

# ============================================================ inter-plane connectors (down)
_close_prev_plane()                      # close the last layer's group
_PLANE_G[0] = None                       # connectors below belong to no plane
vdown(CX, P1 + 350, P2, "①  data  →  ②  decide   (signals + risk budget)", DATA[0])
vdown(CX, P2 + 330, P3, "②  decide  →  ③  execute   (target weights)", STRAT[0])
vdown(CX, P3 + 620, P4, "③  execute  →  ④  heartbeat + commerce", IDENT[0])
vdown(CX, P4 + P4_H, P5, "④  →  ⑤  journal · snapshot · observe", OBS[0])

# ============================================================ self-assert: every layer present
_blob = "\n".join(e["text"] for e in EL if e["type"] == "text")
for tok in ("x402", "ERC-8004", "ERC-8183", "auto-selector", "cmc_stream", "CMC_ONLY",
            "mean-reversion", "TWAK", "agentId 133085", "Regime-Adaptive", "ROADMAP",
            "vlayer", "Web Proof", "provenance_proof", "RegimeVerifier"):
    assert tok in _blob, f"architecture diagram is missing required layer token: {tok!r}"


# ---- self-assert: no text box will wrap/overflow when opened in the Excalidraw app ----
# Stored width must clear Excalidraw's true metric (mono≈0.60, sans≈0.50 per pt/char). Because
# every text is autoResize=False, the app renders inside this exact box — so a box that clears the
# real width guarantees the in-app render matches the SVG (no reflow, no drift).
def _real_w(s, size, family):
    per = 0.60 if family == 3 else 0.50
    return max((len(ln) for ln in s.split("\n")), default=1) * size * per


for _e in EL:
    if _e["type"] == "text":
        assert _e["width"] >= _real_w(_e["text"], _e["fontSize"], _e["fontFamily"]), \
            f"text box too narrow — would wrap in Excalidraw: {_e['text'][:48]!r}"


# ---------------- SVG mirror (deterministic, derived from the SAME elements) ----------------
def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _font(family):
    return "ui-monospace, Menlo, monospace" if family == 3 else "Helvetica, Arial, sans-serif"


def emit_svg(elements):
    xs, ys = [], []
    for e in elements:
        xs += [e["x"], e["x"] + e.get("width", 0)]
        ys += [e["y"], e["y"] + e.get("height", 0)]
    pad = 36
    minx, miny = min(xs) - pad, min(ys) - pad
    W, H = (max(xs) + pad) - minx, (max(ys) + pad) - miny
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx:.0f} {miny:.0f} {W:.0f} {H:.0f}" '
         f'width="{W:.0f}" height="{H:.0f}">',
         f'<rect x="{minx:.0f}" y="{miny:.0f}" width="{W:.0f}" height="{H:.0f}" fill="#ffffff"/>']
    for e in elements:                       # shapes first
        t = e["type"]
        dash = ' stroke-dasharray="8 6"' if e.get("strokeStyle") == "dashed" else ""
        if t == "rectangle":
            rx = 14 if e.get("roundness") else 0
            p.append(f'<rect x="{e["x"]:.1f}" y="{e["y"]:.1f}" width="{e["width"]:.1f}" '
                     f'height="{e["height"]:.1f}" rx="{rx}" fill="{e["backgroundColor"]}" '
                     f'stroke="{e["strokeColor"]}" stroke-width="{e["strokeWidth"]}"{dash}/>')
        elif t == "diamond":
            x, y, w, h = e["x"], e["y"], e["width"], e["height"]
            mx, my = x + w / 2, y + h / 2
            p.append(f'<polygon points="{mx:.1f},{y:.1f} {x + w:.1f},{my:.1f} {mx:.1f},{y + h:.1f} '
                     f'{x:.1f},{my:.1f}" fill="{e["backgroundColor"]}" stroke="{e["strokeColor"]}" '
                     f'stroke-width="{e["strokeWidth"]}"{dash}/>')
        elif t == "ellipse":
            p.append(f'<ellipse cx="{e["x"] + e["width"] / 2:.1f}" cy="{e["y"] + e["height"] / 2:.1f}" '
                     f'rx="{e["width"] / 2:.1f}" ry="{e["height"] / 2:.1f}" fill="{e["backgroundColor"]}" '
                     f'stroke="{e["strokeColor"]}" stroke-width="{e["strokeWidth"]}"{dash}/>')
    for e in elements:                       # arrows
        if e["type"] != "arrow":
            continue
        x1, y1 = e["x"], e["y"]
        x2, y2 = e["x"] + e["width"], e["y"] + e["height"]
        col, sw = e["strokeColor"], e["strokeWidth"]
        dash = ' stroke-dasharray="8 6"' if e.get("strokeStyle") == "dashed" else ""
        p.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{col}" '
                 f'stroke-width="{sw}"{dash}/>')
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        bx, by = x2 - 13 * ux, y2 - 13 * uy
        p.append(f'<polygon points="{x2:.1f},{y2:.1f} {bx - 6 * uy:.1f},{by + 6 * ux:.1f} '
                 f'{bx + 6 * uy:.1f},{by - 6 * ux:.1f}" fill="{col}"/>')
    for e in elements:                       # text on top
        if e["type"] != "text":
            continue
        fs = e["fontSize"]
        weight = "700" if fs >= 18 else ("600" if fs >= 14.5 else "400")
        anchor = {"left": "start", "center": "middle", "right": "end"}.get(e.get("textAlign", "left"), "start")
        x = e["x"] if anchor == "start" else (e["x"] + e["width"] / 2 if anchor == "middle" else e["x"] + e["width"])
        y0 = e["y"] + fs
        lh = fs * e.get("lineHeight", 1.25)
        p.append(f'<text x="{x:.1f}" y="{y0:.1f}" font-size="{fs}" font-family="{_font(e.get("fontFamily", 2))}" '
                 f'font-weight="{weight}" fill="{e["strokeColor"]}" text-anchor="{anchor}" xml:space="preserve">')
        for i, ln in enumerate(e["text"].split("\n")):
            p.append(f'<tspan x="{x:.1f}" dy="{0 if i == 0 else lh:.1f}">{_esc(ln) or " "}</tspan>')
        p.append("</text>")
    p.append("</svg>")
    return "\n".join(p)


# ---------------- write ----------------
# Derive docs/ from THIS file's location (scripts/../docs) so the path can't rot when the repo is
# renamed or cloned (the old hardcoded absolute path silently broke).
DOCS = os.path.join(BASE, "docs")
out = {"type": "excalidraw", "version": 2, "source": "scripts/gen_architecture.py",
       "elements": EL, "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"}, "files": {}}
with open(f"{DOCS}/architecture.excalidraw", "w") as f:
    json.dump(out, f, indent=2)
print(f"wrote {DOCS}/architecture.excalidraw — {len(EL)} elements")

svg = emit_svg(EL)
with open(f"{DOCS}/architecture.svg", "w") as f:
    f.write(svg)
print(f"wrote {DOCS}/architecture.svg — {len(svg)} bytes")
print(f"proof derived live: x402 = {X402_PROOF}  |  commerce = {JOB_PROOF}")

try:
    import cairosvg
    cairosvg.svg2png(bytestring=svg.encode(), write_to=f"{DOCS}/architecture.png", scale=2)
    print(f"wrote {DOCS}/architecture.png (via cairosvg)")
except Exception as exc:
    print(f"PNG skipped ({type(exc).__name__}); architecture.svg is the portable render")
