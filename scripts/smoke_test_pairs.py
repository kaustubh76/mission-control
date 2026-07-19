"""
Fix 9.F — live smoke test for every configured pair on Binance testnet.

What it does for each pair in `settings.pairs`:
  1. Reads min_notional + precision from the exchange.
  2. Computes the smallest legal qty (max of min_qty step and the qty
     that satisfies min_notional at the current ticker price).
  3. Places a market entry.
  4. Immediately places an opposite-side reduceOnly market to flatten.
  5. Confirms `fetch_positions` shows 0 contracts after the flatten.

Records per-pair:
  - leverage_actual, margin_mode_actual
  - precision_amount, precision_price
  - min_notional, smallest_qty, smallest_notional_usdt
  - entry_avg, exit_avg, round_trip_latency_ms
  - status: "ok" | "skipped" | "failed"
  - reason (when not ok)

Writes JSON to data/smoke_pairs_<UTC-date>.json + prints a one-line
table to stdout.

**SAFETY**: refuses to run unless BINANCE_TESTNET=true. Always uses
reduceOnly on the flatten leg so the script can't accidentally hold a
position.

USAGE:
  python3 scripts/smoke_test_pairs.py
  python3 scripts/smoke_test_pairs.py --pair BTC/USDT:USDT   # single pair
  python3 scripts/smoke_test_pairs.py --dry-run              # no orders
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone

from ictbot.exec.binance_live import BinanceLiveBroker
from ictbot.settings import PAIRS, PROJECT_ROOT, settings


def _floor_to_step(value: float, step: float) -> float:
    if not step or step <= 0:
        return value
    return math.floor(value / step) * step


def _smallest_legal_qty(client, pair: str) -> tuple[float, float, float, dict]:
    """Compute the smallest legal qty for `pair`. Returns
    (qty, price, notional, market_dict)."""
    markets = client.load_markets() or {}
    m = markets.get(pair) or {}
    precision = m.get("precision") or {}
    limits = m.get("limits") or {}
    qty_step = float(precision.get("amount") or 0.001)
    min_notional = float((limits.get("cost") or {}).get("min") or 0.0)
    min_qty = float((limits.get("amount") or {}).get("min") or qty_step)
    ticker = client.fetch_ticker(pair) or {}
    price = float(ticker.get("last") or ticker.get("close") or 0.0)
    if price <= 0:
        raise RuntimeError(f"no ticker price for {pair}")
    # qty must satisfy: qty >= min_qty AND qty × price >= min_notional.
    qty_from_notional = (min_notional / price) if min_notional > 0 else 0.0
    raw = max(min_qty, qty_from_notional)
    qty = _floor_to_step(raw, qty_step)
    # If floor undershot the minimum, bump one step.
    while qty * price < min_notional or qty < min_qty:
        qty += qty_step
        # Cap iterations to avoid runaway.
        if qty * price > min_notional * 5:
            break
    return qty, price, qty * price, m


def _smoke_one(client, pair: str, dry_run: bool) -> dict:
    """Test one pair. Returns the per-pair status dict (always — never raises)."""
    out: dict = {
        "pair": pair,
        "status": "ok",
        "reason": None,
        "leverage_actual": None,
        "margin_mode_actual": None,
        "precision_amount": None,
        "precision_price": None,
        "min_notional": None,
        "smallest_qty": None,
        "smallest_notional_usdt": None,
        "entry_avg": None,
        "exit_avg": None,
        "round_trip_latency_ms": None,
    }
    try:
        rows = client.fetch_positions(symbols=[pair]) or []
        for row in rows:
            if row.get("symbol") != pair:
                continue
            lev = row.get("leverage")
            if lev is not None:
                out["leverage_actual"] = int(float(lev))
            info = row.get("info") or {}
            mm = row.get("marginMode") or info.get("marginType")
            if mm:
                out["margin_mode_actual"] = str(mm).lower()
            break
        qty, price, notional, market = _smallest_legal_qty(client, pair)
        out["precision_amount"] = (market.get("precision") or {}).get("amount")
        out["precision_price"] = (market.get("precision") or {}).get("price")
        out["min_notional"] = ((market.get("limits") or {}).get("cost") or {}).get("min")
        out["smallest_qty"] = qty
        out["smallest_notional_usdt"] = notional
        if dry_run:
            out["status"] = "skipped"
            out["reason"] = "--dry-run"
            return out

        # Place entry.
        t0 = time.perf_counter()
        entry = client.create_order(pair, "market", "buy", qty, None, {})
        out["entry_avg"] = entry.get("average") or entry.get("price")
        # Immediate reduceOnly flatten.
        exit_order = client.create_order(
            pair, "market", "sell", qty, None, {"reduceOnly": True}
        )
        out["exit_avg"] = exit_order.get("average") or exit_order.get("price")
        out["round_trip_latency_ms"] = (time.perf_counter() - t0) * 1000

        # Confirm flat.
        time.sleep(0.5)  # short settle before re-fetch
        rows = client.fetch_positions(symbols=[pair]) or []
        for row in rows:
            if row.get("symbol") != pair:
                continue
            contracts = float(row.get("contracts") or 0)
            if contracts == 0:
                try:
                    contracts = abs(
                        float((row.get("info") or {}).get("positionAmt") or 0)
                    )
                except Exception:
                    contracts = 0
            if contracts > 0:
                out["status"] = "failed"
                out["reason"] = f"position still open after flatten: {contracts}"
            break
    except Exception as exc:
        out["status"] = "failed"
        out["reason"] = str(exc)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument(
        "--pair",
        default=None,
        help="Test a single pair (default: all configured pairs)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute readiness numbers but place no orders",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default data/smoke_pairs_<UTC-date>.json)",
    )
    args = ap.parse_args()

    if not settings.binance_testnet:
        print("❌ Refusing to run: BINANCE_TESTNET=false (set true for safety)")
        return 2

    if not (settings.binance_api_key and settings.binance_api_secret):
        print("❌ BINANCE_API_KEY / BINANCE_API_SECRET missing in .env")
        return 2

    pairs = [args.pair] if args.pair else list(PAIRS)
    broker = BinanceLiveBroker(
        allowed_pairs=set(pairs),
        testnet=True,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )
    client = broker._client

    print(f"Smoke test on {len(pairs)} pair(s) (dry_run={args.dry_run})\n")
    results = [_smoke_one(client, p, args.dry_run) for p in pairs]

    # JSON output.
    out_dir = PROJECT_ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.out is None:
        today = datetime.now(timezone.utc).date().isoformat()
        out_path = out_dir / f"smoke_pairs_{today}.json"
    else:
        out_path = PROJECT_ROOT / args.out
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "binance_testnet": settings.binance_testnet,
        "results": results,
    }
    out_path.write_text(json.dumps(report, indent=2))

    # Stdout table.
    print(
        f"{'pair':<22}{'status':<10}{'lev':>5}{'margin':>10}{'qty':>12}"
        f"{'notional':>12}{'latency_ms':>12}"
    )
    print("-" * 88)
    fail_count = 0
    for r in results:
        if r["status"] == "failed":
            fail_count += 1
        lev = r.get("leverage_actual") or "?"
        mm = (r.get("margin_mode_actual") or "?")[:8]
        q = r.get("smallest_qty")
        q_s = f"{q:g}" if q else "?"
        n = r.get("smallest_notional_usdt")
        n_s = f"${n:.2f}" if n else "?"
        lat = r.get("round_trip_latency_ms")
        lat_s = f"{lat:.0f}" if lat else "—"
        status_s = r["status"]
        if r["reason"]:
            status_s += f"({r['reason'][:25]})"
        print(
            f"{r['pair']:<22}{status_s:<10}{lev!s:>5}{mm:>10}{q_s:>12}"
            f"{n_s:>12}{lat_s:>12}"
        )
    print()
    print(f"Report: {out_path}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
