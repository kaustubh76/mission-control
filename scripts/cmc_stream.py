#!/usr/bin/env python3
"""CMC-native 4h candle streamer — the CoinMarketCap-sourced candle feed for `momentum_cmc`.

Subscribes to the CMC Pro WebSocket (`market@crypto_latest_price`, `full` mode — Startup+) for the 8
contest tokens and rolls the real-time price ticks into **4h OHLC candles**, persisted to the shared
parquet cache (`data/cache/cmc/<SYM>_USDT/4h.parquet`) so the allocator (candle_source="cmc_4h") decides
on CMC's own data — no exchange candles. CMC has no historical intraday OHLCV on our tier, so we ACCUMULATE
it from the live feed.

Verified subscribe frame (the crypto-ID key is `crypto_ids`):
    {"method":"subscribe","channel":"market@crypto_latest_price","params":{"mode":"full","crypto_ids":[...]}}
Data frames: {"type":"data","data":{"cid":<id>,"p":<price>,"vu":<vol24h>,...},"ts":<epoch_ms>}

Long-running: auto-reconnect with backoff, WS keepalive ping, heartbeat file, append log. The in-progress
bar is checkpointed so a restart never loses the current 4h bucket. Run continuously (nohup/launchd); a
watchdog cron can restart it if the heartbeat goes stale. Never logs the API key.

Usage:
    PYTHONPATH=src python scripts/cmc_stream.py            # continuous
    PYTHONPATH=src python scripts/cmc_stream.py --once 10  # collect N ticks then exit (smoke test)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ictbot.data import cache  # noqa: E402
from ictbot.data import cmc_onchain  # noqa: E402
from ictbot.data.cmc_agent_hub import CMC_IDS  # noqa: E402
from ictbot.data.cmc_onchain import onchain_tokens  # noqa: E402
from ictbot.settings import CMC_WS_DIR, DATA_DIR, JOURNAL_DIR, settings  # noqa: E402

WS_URL = "wss://pro-stream.coinmarketcap.com/v1"
CHANNEL = "market@crypto_latest_price"
BAR_SECONDS = 4 * 3600  # 4h bars on the UTC grid (00/04/08/12/16/20)
ID2SYM = {v: k for k, v in CMC_IDS.items()}
LOG = DATA_DIR / "logs" / "cmc_stream.log"
HEARTBEAT = DATA_DIR / "logs" / "cmc_stream_heartbeat.ts"
PARTIAL = DATA_DIR / "cache" / "cmc_4h_partial.json"
CAP_PATH = JOURNAL_DIR / "cmc_ws_capability.json"  # Phase-0 channel/field/param discovery map
SNAPSHOT = CMC_WS_DIR / "quotes.json"  # per-token latest CMC-WS quote harvest (shared dir)
SNAPSHOT_THROTTLE_S = 30  # don't rewrite the snapshot on every ~15s tick

# CEX `market@crypto_latest_price` full-mode fields → our snapshot keys. CONFIRMED live by the
# Phase-0 probe (BNB id 1839): price + market cap + circulating supply + 24h volume + the full
# percent-change window family. `fdv24h` is intentionally omitted (it mirrored p24h in the probe,
# so its meaning is ambiguous — we don't surface a field we can't trust).
_QUOTE_FIELDS = {
    "p": "price",
    "mc": "market_cap",
    "cs": "circulating_supply",
    "vu": "volume_24h",
    "p24h": "pct_24h",
    "p7d": "pct_7d",
    "p30d": "pct_30d",
    "p60d": "pct_60d",
    "p3m": "pct_3m",
    "p1y": "pct_1y",
    "pytd": "pct_ytd",
    "pall": "pct_all",
}

# Phase-0 discovery: the candidate channels we sweep + best-effort subscribe params. The CEX
# channel is the one we already run; the `onchain@*` family is tier/param-gated, so the probe
# records what the server actually returns (ack/error/data) instead of trusting reverse-engineered
# docs. The on-chain probe uses a representative BSC token (CAKE) just to elicit the wire shape;
# the real per-token pool mapping (Phase 2) is derived from CMC's own data, not these constants.
BSC_PLATFORM_ID = 14  # CMC platform id for BNB Smart Chain
_PROBE_TOKEN_ADDR = "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"  # CAKE on BSC (representative)
CANDIDATE_CHANNELS = (
    CHANNEL,
    "onchain@kline",
    "onchain@token_metric",
    "onchain@unique_trader",
    "onchain@liquidity_event",
    "onchain@holders_metrics",
)

# The on-chain channels we ingest (Phase 2) — all confirmed to emit with ONLY a token contract
# address (no pool address). token_metric carries the strategy-relevant flow (buy/sell volume,
# unique traders, txn counts); holders = concentration; liquidity_event = add/remove/migrate.
ONCHAIN_DIR = CMC_WS_DIR  # shared on-chain snapshot dir (see settings.CMC_WS_DIR)
# Token-address-keyed channels (params: platform_id + address) — work for all BSC universe tokens.
ONCHAIN_TOKEN_CHANNELS = (
    "onchain@token_metric",      # buy/sell vol, unique traders, txns, h/l per window
    "onchain@holders_metrics",   # top-N holder concentration
    "onchain@liquidity_event",   # liquidity add/remove/migrate (USD)
    "onchain@token_agg_event",   # aggregated price + total token liquidity (`lu`)
    "onchain@transaction",       # per-swap firehose → aggregated into whale-flow
)
# NOTE: `onchain@pool_metric` (per-pool unique traders) was dropped — it streamed fine but no
# strategy/overlay ever consumed it (its store reader had zero callers), so subscribing + parsing +
# writing it every tick was pure dead weight. All STRATEGY-relevant on-chain signals come from the
# token-keyed channels above.
ONCHAIN_LIQ_KEEP = 25       # recent liquidity events retained per token
WHALE_KEEP = 200            # recent large swaps retained per token (in-memory ring)
WHALE_WINDOW_S = 3600       # rolling whale-flow window (seconds)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _bar_start(ts_ms: int) -> int:
    """Epoch-seconds start of the 4h bucket containing ts_ms (UTC 4h grid)."""
    return (ts_ms // 1000 // BAR_SECONDS) * BAR_SECONDS


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


# --------------------------------------------------------------------------- #
# Phase 0 — channel discovery probe (read-only: no cache/bar/heartbeat writes)
# --------------------------------------------------------------------------- #
def _probe_params(channel: str, crypto_id: int | None) -> dict:
    """Best-effort subscribe params per channel for discovery."""
    if channel == CHANNEL:  # market@crypto_latest_price
        return {"mode": "full", "crypto_ids": [crypto_id or next(iter(CMC_IDS.values()))]}
    if channel == "onchain@kline":
        return {"platform_id": BSC_PLATFORM_ID, "address": _PROBE_TOKEN_ADDR, "interval": "1h"}
    if channel.startswith("onchain@"):
        return {"platform_id": BSC_PLATFORM_ID, "address": _PROBE_TOKEN_ADDR}
    return {}


async def _probe_once(channel: str, params: dict, timeout: float = 20.0) -> dict:
    """Subscribe to `channel` and collect the first ack/error + first data frame within
    `timeout`. Returns a capability record; never raises. `ok` = subscribable on this tier
    (acked, no error); `has_data` = a real data frame arrived (so `fields`/`sample` are real)."""
    import websockets

    key = settings.cmc_api_key
    rec: dict = {
        "channel": channel,
        "params_used": params,
        "ok": False,
        "acked": False,
        "has_data": False,
        "fields": [],
        "ack": None,
        "error": None,
        "sample": None,
    }
    sub = {"method": "subscribe", "channel": channel, "params": params}
    try:
        async with websockets.connect(
            f"{WS_URL}?CMC_PRO_API_KEY={key}", open_timeout=15, ping_interval=20, ping_timeout=20
        ) as ws:
            await ws.send(json.dumps(sub))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.5, deadline - time.time()))
                except asyncio.TimeoutError:
                    break
                d = json.loads(raw)
                t = d.get("type")
                if t == "ack":
                    rec["acked"] = True
                    rec["ack"] = d.get("params") or d.get("data") or {}
                elif t == "error":
                    rec["error"] = d.get("status") or d.get("data") or d
                    break
                elif t == "data":
                    x = d.get("data") or {}
                    rec["has_data"] = True
                    rec["fields"] = sorted(x.keys()) if isinstance(x, dict) else []
                    rec["sample"] = x
                    break
    except Exception as e:  # noqa: BLE001 — discovery must never crash the run
        rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    rec["ok"] = rec["acked"] and rec["error"] is None
    return rec


async def _probe_print(channel: str, crypto_id: int | None, timeout: float) -> int:
    """`--probe`: pretty-print one channel's capability record (every key/value), exit."""
    rec = await _probe_once(channel, _probe_params(channel, crypto_id), timeout=timeout)
    print(json.dumps(rec, indent=2, sort_keys=True, default=str))
    return 0 if rec["ok"] else 2


async def _discover(timeout: float) -> dict:
    """`--discover`: sweep CANDIDATE_CHANNELS, write/merge the capability map that gates which
    channels the daemon subscribes to (mirrors cmc_intel's cmc_capability.json pattern)."""
    cap: dict = {}
    for ch in CANDIDATE_CHANNELS:
        rec = await _probe_once(ch, _probe_params(ch, None), timeout=timeout)
        cap[ch] = {
            "ok": rec["ok"],
            "acked": rec["acked"],
            "has_data": rec["has_data"],
            "fields": rec["fields"],
            "params_used": rec["params_used"],
            "error": rec["error"],
        }
        _log(
            f"probe {ch}: ok={rec['ok']} data={rec['has_data']} "
            f"fields={rec['fields'][:10]} err={str(rec['error'])[:70]}"
        )
    CAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CAP_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cap, indent=2, default=str), encoding="utf-8")
    tmp.replace(CAP_PATH)
    _log(f"wrote capability map {CAP_PATH} ({sum(1 for v in cap.values() if v['ok'])}/{len(cap)} ok)")
    return cap


class BarBuilder:
    """Per-token in-progress 4h OHLC bar; finalizes the completed bar to the cache on rollover.

    The current bucket is checkpointed to PARTIAL so a restart resumes the same bar instead of dropping
    the partial. `cache.write` merges + dedups on `time`, so re-finalizing a bar is idempotent."""

    def __init__(self) -> None:
        try:
            self.bars: dict = json.loads(PARTIAL.read_text(encoding="utf-8"))
        except Exception:
            self.bars = {}

    def _save(self) -> None:
        PARTIAL.parent.mkdir(parents=True, exist_ok=True)
        tmp = PARTIAL.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.bars), encoding="utf-8")
        tmp.replace(PARTIAL)

    def on_tick(self, sym: str, price: float, vol: float, ts_ms: int) -> bool:
        """Fold a price tick into sym's current 4h bar. Returns True if a bar was finalized."""
        start = _bar_start(ts_ms)
        b = self.bars.get(sym)
        finalized = False
        if b and b["start"] != start:
            self._finalize(sym, b)  # the next bucket began -> the previous bar is complete
            finalized = True
            b = None
        if b is None:
            b = {"start": start, "open": price, "high": price, "low": price, "close": price, "vol": vol}
        else:
            b["high"] = max(b["high"], price)
            b["low"] = min(b["low"], price)
            b["close"] = price
            b["vol"] = vol
        self.bars[sym] = b
        self._save()
        return finalized

    def _finalize(self, sym: str, b: dict) -> None:
        df = pd.DataFrame(
            [
                {
                    "time": pd.Timestamp(b["start"], unit="s"),
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "volume": float(b.get("vol") or 0.0),
                }
            ]
        )
        cache.write("cmc", f"{sym}/USDT", "4h", df)
        _log(
            f"finalized {sym} 4h bar {pd.Timestamp(b['start'], unit='s')}: "
            f"O{b['open']:.4g} H{b['high']:.4g} L{b['low']:.4g} C{b['close']:.4g}"
        )


class QuoteSnapshotWriter:
    """Per-token latest CMC quote fields harvested from the SAME full-mode frame `BarBuilder`
    consumes — market cap, circulating supply, 24h volume, and the percent-change window family.
    Strictly additive: shares no state with `BarBuilder`, writes a different file, and is wrapped
    best-effort at the call site so a bad field can never regress the 4h bar path or the heartbeat.

    The snapshot lets `cmc_intel.token_changes` serve the universe tilt from local data (0 credits)
    instead of a REST quotes/latest call, and feeds the dashboard. Atomic write (tmp + replace),
    throttled to SNAPSHOT_THROTTLE_S; the first frame always flushes so the file exists promptly."""

    def __init__(self) -> None:
        try:
            existing = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            self.tokens: dict = existing.get("tokens", {}) if isinstance(existing, dict) else {}
        except Exception:
            self.tokens = {}
        self._last_save = 0.0

    def on_frame(self, sym: str, frame: dict, ts_ms: int) -> None:
        """Harvest present `_QUOTE_FIELDS` from `frame` into sym's record (missing keys omitted)."""
        rec: dict = {"ts": int(ts_ms)}
        for src, dst in _QUOTE_FIELDS.items():
            v = frame.get(src)
            if isinstance(v, (int, float)):
                rec[dst] = float(v)
        self.tokens[sym] = rec
        now = time.time()
        if now - self._last_save >= SNAPSHOT_THROTTLE_S:
            self.flush()
            self._last_save = now

    def flush(self) -> None:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        updated = max((int(r.get("ts", 0)) for r in self.tokens.values()), default=0)
        tmp = SNAPSHOT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"updated_ms": updated, "tokens": self.tokens}), encoding="utf-8")
        tmp.replace(SNAPSHOT)


class OnchainWriter:
    """Harvests the `onchain@*` data frames (token_metric / holders / liquidity_event) for the
    mapped real-ERC20 subset into per-channel JSON snapshots. Runs on a SEPARATE connection from
    the bar feed, so it shares no state with `BarBuilder`/`QuoteSnapshotWriter` and cannot regress
    the contest-critical 4h path. Frames carry no channel tag, so each is classified by shape
    (`cmc_onchain.classify_frame`) and routed to its token by contract address. Atomic, throttled."""

    def __init__(self) -> None:
        self.metric = self._load("onchain_token_metric.json")
        self.holders = self._load("onchain_holders.json")
        self.liquidity = self._load("onchain_liquidity.json")
        self.token_agg = self._load("onchain_token_agg.json")
        self.whale = self._load("onchain_whale.json")
        self.addr2sym = {t["address"].lower(): s for s, t in onchain_tokens().items()}
        self._whale_events: dict[str, list] = {}  # sym -> [(ts_ms, signed_value_usd)]
        self._whale_usd = float(getattr(settings, "onchain_whale_usd", 10000.0) or 10000.0)
        self._dirty: set[str] = set()
        self._last_save = 0.0

    @staticmethod
    def _load(name: str) -> dict:
        try:
            d = json.loads((ONCHAIN_DIR / name).read_text(encoding="utf-8"))
            return d.get("tokens", {}) if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _sym_for(self, data: dict) -> str | None:
        for k in ("a", "t0a", "t1a"):  # token_metric/holders/token_agg use `a`; tx uses t0a/t1a
            v = data.get(k)
            if isinstance(v, str) and v.lower() in self.addr2sym:
                return self.addr2sym[v.lower()]
        return None

    def _on_whale(self, sym: str, tx: dict, ts_ms: int) -> None:
        """Aggregate a swap into sym's rolling whale-flow: keep only large swaps (>= threshold),
        net buys(+)/sells(-) over WHALE_WINDOW_S, store count + net USD."""
        v = tx.get("value_usd") or 0.0
        if v < self._whale_usd:
            return
        signed = v if tx.get("type") == "buy" else -v
        ev = self._whale_events.setdefault(sym, [])
        ev.append((int(ts_ms), signed))
        del ev[:-WHALE_KEEP]
        cutoff = _now_ms() - WHALE_WINDOW_S * 1000
        win = [(t, s) for t, s in ev if t >= cutoff]
        self.whale[sym] = {"ts": int(ts_ms), "whale_net_usd": round(sum(s for _, s in win), 2),
                           "whale_count": len(win), "window_s": WHALE_WINDOW_S}
        self._dirty.add("whale")

    def on_frame(self, data: dict, ts_ms: int) -> None:
        kind = cmc_onchain.classify_frame(data)
        sym = self._sym_for(data)
        if not sym:
            return
        if kind == "token_metric":
            p = cmc_onchain.parse_token_metric(data)
            if p:
                self.metric[sym] = {"ts": int(ts_ms), **p}
                self._dirty.add("metric")
        elif kind == "holders":
            p = cmc_onchain.parse_holders(data)
            if p:
                self.holders[sym] = {"ts": int(ts_ms), **p}
                self._dirty.add("holders")
        elif kind == "token_agg":
            p = cmc_onchain.parse_token_agg(data)
            if p:
                self.token_agg[sym] = {"ts": int(ts_ms), **p}
                self._dirty.add("token_agg")
        elif kind == "transaction":
            p = cmc_onchain.parse_transaction(data)
            if p:
                self._on_whale(sym, p, ts_ms)
        elif kind == "liquidity":
            p = cmc_onchain.parse_liquidity_event(data)
            if p:
                rec = self.liquidity.get(sym) or {"events": []}
                rec["events"] = ([{"ts": int(ts_ms), **p}] + rec.get("events", []))[:ONCHAIN_LIQ_KEEP]
                rec["ts"] = int(ts_ms)
                self.liquidity[sym] = rec
                self._dirty.add("liquidity")
        now = time.time()
        if now - self._last_save >= SNAPSHOT_THROTTLE_S:
            self.flush()
            self._last_save = now

    def flush(self) -> None:
        ONCHAIN_DIR.mkdir(parents=True, exist_ok=True)
        for tag, name, store in (
            ("metric", "onchain_token_metric.json", self.metric),
            ("holders", "onchain_holders.json", self.holders),
            ("liquidity", "onchain_liquidity.json", self.liquidity),
            ("token_agg", "onchain_token_agg.json", self.token_agg),
            ("whale", "onchain_whale.json", self.whale),
        ):
            if tag not in self._dirty:
                continue
            try:
                updated = max((int(r.get("ts", 0)) for r in store.values()), default=0)
                tmp = (ONCHAIN_DIR / name).with_suffix(".json.tmp")
                tmp.write_text(json.dumps({"updated_ms": updated, "tokens": store}), encoding="utf-8")
                tmp.replace(ONCHAIN_DIR / name)
            except Exception as e:  # noqa: BLE001 — surface a disk error; keep `tag` dirty to retry
                _log(f"onchain snapshot write failed ({name}): {type(e).__name__}: {str(e)[:80]}")
                continue
            self._dirty.discard(tag)  # cleared per-success → failed tags stay dirty, retried next flush


def _channel_subscribable(channel: str) -> bool:
    """True unless the Phase-0 capability map explicitly marked `channel` out-of-tier."""
    try:
        rec = json.loads(CAP_PATH.read_text(encoding="utf-8")).get(channel)
        return True if rec is None else bool(rec.get("ok"))
    except Exception:
        return True


async def _stream_onchain(writer: OnchainWriter, once_n: int = 0) -> int:
    """Isolated on-chain feed: subscribes the ONCHAIN_CHANNELS for each mapped token on its OWN
    WS connection and routes frames to `writer`. Separate from the CEX bar feed by design — a
    failure/flood here can never touch the 4h candle path."""
    import websockets

    key = settings.cmc_api_key
    toks = onchain_tokens()
    # token-keyed channels: one sub per token (platform_id + address). All STRATEGY-relevant on-chain
    # signals are token-keyed; the per-pool channel was dropped as dead weight (no consumer).
    subs = [
        {"method": "subscribe", "channel": ch,
         "params": {"platform_id": t["platform_id"], "address": t["address"]}}
        for ch in ONCHAIN_TOKEN_CHANNELS if _channel_subscribable(ch)
        for t in toks.values()
    ]
    if not subs:
        _log("onchain: no subscribable channels/tokens; idle")
        return 0
    n = 0
    async with websockets.connect(
        f"{WS_URL}?CMC_PRO_API_KEY={key}", open_timeout=15, ping_interval=20, ping_timeout=20
    ) as ws:
        for s in subs:
            await ws.send(json.dumps(s))
        _log(f"onchain subscribed: {len(subs)} subs across {sorted(toks)} "
             f"({len(ONCHAIN_TOKEN_CHANNELS)} token channels)")
        async for raw in ws:
            d = json.loads(raw)
            t = d.get("type")
            if t == "data":
                try:
                    writer.on_frame(d.get("data") or {}, int(d.get("ts") or _now_ms()))
                except Exception:  # noqa: BLE001 — never let a bad frame kill the feed
                    pass
                n += 1
                if once_n and n >= once_n:
                    writer.flush()
                    return n
            elif t == "error":
                _log(f"onchain error frame: {json.dumps(d.get('status') or d)[:160]}")
    return n


async def _stream(builder: BarBuilder, snap: "QuoteSnapshotWriter | None" = None, once_n: int = 0) -> int:
    import websockets

    key = settings.cmc_api_key
    sub = {
        "method": "subscribe",
        "channel": CHANNEL,
        "params": {"mode": "full", "crypto_ids": list(CMC_IDS.values())},
    }
    n = 0
    async with websockets.connect(
        f"{WS_URL}?CMC_PRO_API_KEY={key}", open_timeout=15, ping_interval=20, ping_timeout=20
    ) as ws:
        await ws.send(json.dumps(sub))
        _log(f"subscribed (full mode, {len(CMC_IDS)} tokens)")
        async for raw in ws:
            d = json.loads(raw)
            t = d.get("type")
            if t == "data":
                x = d.get("data") or {}
                sym = ID2SYM.get(x.get("cid"))
                p = x.get("p")
                ts = d.get("ts") or _now_ms()
                if sym and isinstance(p, (int, float)) and p > 0:
                    builder.on_tick(sym, float(p), float(x.get("vu") or 0.0), int(ts))
                    if snap is not None:
                        try:
                            snap.on_frame(sym, x, int(ts))  # additive harvest, never breaks bars
                        except Exception:  # noqa: BLE001
                            pass
                    HEARTBEAT.write_text(str(_now_ms()), encoding="utf-8")
                    n += 1
                    if once_n and n >= once_n:
                        if snap is not None:
                            snap.flush()
                        return n
            elif t == "error":
                _log(f"error frame: {json.dumps(d.get('status') or d)[:160]}")
            elif t == "ack":
                _log(f"ack: {json.dumps(d.get('params') or {})[:120]}")
    return n


async def _supervise(name: str, factory) -> None:
    """Run a stream coroutine forever, reconnecting with capped backoff on any failure. One per
    connection, so the CEX bar feed and the on-chain feed fail + recover independently."""
    backoff = 1.0
    while True:
        try:
            await factory()
        except Exception as e:  # noqa: BLE001 — any WS/parse failure -> reconnect
            _log(f"{name} disconnected ({type(e).__name__}: {str(e)[:90]}); reconnect in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        else:
            backoff = 1.0


async def _run_forever(
    builder: BarBuilder,
    snap: "QuoteSnapshotWriter | None" = None,
    onchain: "OnchainWriter | None" = None,
) -> None:
    tasks = [_supervise("cex", lambda: _stream(builder, snap))]
    if onchain is not None:
        tasks.append(_supervise("onchain", lambda: _stream_onchain(onchain)))
    await asyncio.gather(*tasks)


def main() -> int:
    ap = argparse.ArgumentParser(description="CMC-native 4h candle streamer (WebSocket).")
    ap.add_argument("--once", type=int, default=0, help="collect N ticks then exit (smoke test)")
    ap.add_argument("--onchain-once", type=int, default=0, help="collect N onchain frames then exit (smoke)")
    ap.add_argument("--probe", action="store_true", help="probe one channel, print its first frame, exit")
    ap.add_argument("--discover", action="store_true", help="sweep candidate channels → capability map, exit")
    ap.add_argument("--probe-channel", default=CHANNEL, help="channel to probe (default: CEX price)")
    ap.add_argument("--probe-id", type=int, default=0, help="crypto id for the probe (CEX channel)")
    ap.add_argument("--probe-timeout", type=float, default=20.0, help="seconds to wait per channel probe")
    args = ap.parse_args()
    if not settings.cmc_api_key:
        _log("no CMC_API_KEY configured; aborting")
        return 2
    # Probe/discover are read-only (no bar/cache/heartbeat writes) — handle before BarBuilder().
    if args.discover:
        asyncio.run(_discover(args.probe_timeout))
        return 0
    if args.probe:
        return asyncio.run(_probe_print(args.probe_channel, args.probe_id or None, args.probe_timeout))
    if args.onchain_once:
        n = asyncio.run(_stream_onchain(OnchainWriter(), once_n=args.onchain_once))
        _log(f"--onchain-once collected {n} frame(s); onchain snapshots checkpointed")
        return 0
    builder = BarBuilder()
    snap = QuoteSnapshotWriter()
    if args.once:
        n = asyncio.run(_stream(builder, snap, once_n=args.once))
        _log(f"--once collected {n} tick(s); partial bars + quote snapshot checkpointed")
        return 0
    onchain = OnchainWriter() if settings.cmc_onchain_enabled else None
    _log(f"cmc_stream starting (continuous) — {sorted(CMC_IDS)}"
         + (f" + onchain {sorted(onchain_tokens())}" if onchain else ""))
    asyncio.run(_run_forever(builder, snap, onchain))
    return 0


if __name__ == "__main__":
    sys.exit(main())
