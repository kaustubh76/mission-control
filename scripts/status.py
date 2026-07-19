"""
Fix 13.C (plan: Phase 13 ops dashboard) — one-shot ops snapshot.

Reads (no writes, no orders) and pretty-prints five sections:

  1. Wallet      — free USDT from Binance + delta since baseline
  2. Positions   — open positions per pair (broker fetch_positions)
  3. Smoke gate  — 4-pair acceptance gate (reuses
                   `diagnose_live_pnl.build_smoke_gate`)
  4. Heartbeat   — wall-clock age of `data/logs/heartbeat.ts`
  5. Closes      — last 5 broker-truth journal rows

USAGE:
  python3 scripts/status.py            # pretty-printed
  python3 scripts/status.py --json     # machine-readable

Or via the Makefile wrapper:
  make status
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = REPO_ROOT / "data" / "journal" / "signals.json"
HEARTBEAT_PATH = REPO_ROOT / "data" / "logs" / "heartbeat.ts"
WALLET_BASELINE_PATH = REPO_ROOT / "data" / "wallet_baseline_usdt.txt"


def _load_diagnose_module():
    """Import diagnose_live_pnl.py as a module so we can reuse its
    classifier without copy-pasting. Mirrors the test pattern from
    tests/test_diagnose_live_pnl.py."""
    spec = importlib.util.spec_from_file_location(
        "diagnose_live_pnl", REPO_ROOT / "scripts" / "diagnose_live_pnl.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_journal() -> list[dict]:
    if not JOURNAL_PATH.exists():
        return []
    with open(JOURNAL_PATH) as f:
        return json.load(f)


def _read_baseline() -> float | None:
    if not WALLET_BASELINE_PATH.exists():
        return None
    try:
        return float(WALLET_BASELINE_PATH.read_text().strip())
    except (ValueError, OSError):
        return None


def _heartbeat_age_s() -> tuple[float | None, str | None]:
    """Return (age_seconds, last_iso). None if heartbeat file missing
    or unparseable."""
    if not HEARTBEAT_PATH.exists():
        return None, None
    try:
        raw = HEARTBEAT_PATH.read_text().strip()
        # heartbeat.ts can be epoch seconds OR an ISO timestamp.
        try:
            ts = float(raw)
            last_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except ValueError:
            last_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - last_dt).total_seconds()
        return age, last_dt.isoformat()
    except (ValueError, OSError):
        return None, None


def _wallet_section(broker) -> dict:
    """Free USDT + delta since baseline."""
    try:
        equity = float(broker.equity())
    except Exception as exc:
        return {"equity_usdt": None, "error": str(exc)}
    baseline = _read_baseline()
    delta = (equity - baseline) if baseline is not None else None
    return {
        "equity_usdt": equity,
        "baseline_usdt": baseline,
        "delta_usdt": delta,
    }


def _positions_section(broker, pairs: list[str]) -> list[dict]:
    """Open positions per pair (contracts > 0)."""
    try:
        rows = broker._client.fetch_positions(symbols=sorted(pairs)) or []
    except Exception as exc:
        return [{"error": str(exc)}]
    out = []
    for row in rows:
        contracts = float(row.get("contracts") or 0)
        if contracts == 0:
            try:
                contracts = abs(float((row.get("info") or {}).get("positionAmt") or 0))
            except Exception:
                contracts = 0.0
        if contracts <= 0:
            continue
        side = (row.get("side") or "").lower()
        out.append(
            {
                "pair": row.get("symbol"),
                "side": "BUY" if side == "long" else "SELL",
                "contracts": contracts,
                "entry_price": float(row.get("entryPrice") or 0),
                "mark_price": float(row.get("markPrice") or 0),
                "unrealized_usdt": float(row.get("unrealizedPnl") or 0),
            }
        )
    return out


def _smoke_gate_section(rows: list[dict], pairs: list[str], diagnose_mod) -> dict:
    return diagnose_mod.build_smoke_gate(rows, list(pairs))


def _heartbeat_section() -> dict:
    age, last_iso = _heartbeat_age_s()
    if age is None:
        return {"age_seconds": None, "last_iso": None, "missing": True}
    return {"age_seconds": age, "last_iso": last_iso, "missing": False}


def _recent_closes(rows: list[dict], limit: int = 5) -> list[dict]:
    """Last `limit` broker-truth closes, newest-first."""
    closed = [
        r for r in rows
        if r.get("outcome") in ("WIN", "LOSS", "BE", "CLOSED")
        and r.get("broker") not in (None, "paper")
        and r.get("pnl_r") is not None
    ]
    closed.sort(key=lambda r: r.get("closed_ts") or "", reverse=True)
    out = []
    for r in closed[:limit]:
        out.append(
            {
                "pair": r.get("pair"),
                "outcome": r.get("outcome"),
                "close_reason": r.get("close_reason"),
                "closed_ts": r.get("closed_ts"),
                "pnl_r": r.get("pnl_r"),
                "fees_paid": r.get("fees_paid"),
            }
        )
    return out


def build_status(
    *, broker, pairs: list[str], diagnose_mod, journal_rows: list[dict]
) -> dict:
    """Pure aggregate of the five sections. Caller wires in the broker
    + diagnose module (tests inject mocks)."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "wallet": _wallet_section(broker),
        "positions": _positions_section(broker, pairs),
        "smoke_gate": _smoke_gate_section(journal_rows, pairs, diagnose_mod),
        "heartbeat": _heartbeat_section(),
        "recent_closes": _recent_closes(journal_rows),
    }


def _print_human(status: dict) -> None:
    print("=" * 72)
    print(f"ops status — {status['generated_at']}")
    print("=" * 72)

    # --- Wallet ---
    w = status["wallet"]
    print("\n[Wallet]")
    if w.get("equity_usdt") is None:
        print(f"  ERROR: {w.get('error', 'unavailable')}")
    else:
        line = f"  free USDT = ${w['equity_usdt']:.2f}"
        if w.get("delta_usdt") is not None:
            sign = "+" if w["delta_usdt"] >= 0 else ""
            line += f"   delta vs baseline = {sign}${w['delta_usdt']:.2f}"
        elif w.get("baseline_usdt") is None:
            line += "   (no baseline; run scripts/verify_wallet_parity.py to initialise)"
        print(line)

    # --- Positions ---
    print("\n[Open positions]")
    pos = status["positions"]
    if not pos or (len(pos) == 1 and pos[0].get("error")):
        if pos and pos[0].get("error"):
            print(f"  ERROR: {pos[0]['error']}")
        else:
            print("  (none — all flat)")
    else:
        for p in pos:
            sign = "+" if p["unrealized_usdt"] >= 0 else ""
            print(
                f"  {p['pair']:<22} {p['side']:<5} qty={p['contracts']:g}  "
                f"entry=${p['entry_price']:.4f}  mark=${p['mark_price']:.4f}  "
                f"uPnL={sign}${p['unrealized_usdt']:.2f}"
            )

    # --- Smoke gate ---
    print("\n[4-pair smoke gate]")
    g = status["smoke_gate"]
    verdict = "PASS" if g["smoke_gate_pass"] else "PENDING"
    print(f"  verdict = {verdict}")
    print(f"  passed  ({len(g['pairs_passed'])}): {g['pairs_passed']}")
    print(f"  pending ({len(g['pairs_pending'])}): {g['pairs_pending']}")

    # --- Heartbeat ---
    print("\n[Heartbeat]")
    h = status["heartbeat"]
    if h["missing"]:
        print("  (no heartbeat file — scanner not running?)")
    else:
        age = h["age_seconds"]
        warn = " ⚠ STALE" if age > 300 else ""
        print(f"  age = {age:.0f}s   last = {h['last_iso']}{warn}")

    # --- Recent closes ---
    print("\n[Last 5 broker-truth closes]")
    closes = status["recent_closes"]
    if not closes:
        print("  (none yet — smoke gate is pending broker-truth closes)")
    else:
        for c in closes:
            r = c.get("pnl_r") or 0.0
            sign = "+" if r >= 0 else ""
            fee = c.get("fees_paid")
            fee_s = f"fee=${fee:.4f}" if fee is not None else "fee=n/a"
            reason = c.get("close_reason") or "—"
            print(
                f"  {c['closed_ts']}  {c['pair']:<22} {c['outcome']:<5} "
                f"reason={reason:<6}  R={sign}{r:.3f}  {fee_s}"
            )
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of pretty-printed text.",
    )
    args = ap.parse_args()

    # Lazy-load to avoid pulling ccxt + settings if tests want to import
    # this module's helpers without touching the real broker.
    from ictbot.exec.binance_live import BinanceLiveBroker
    from ictbot.settings import settings

    diagnose_mod = _load_diagnose_module()

    if not settings.binance_testnet:
        # Defensive: status is testnet-shaped (wallet baseline is the
        # testnet's). On mainnet, operators should use venue dashboards.
        print(
            "⚠ BINANCE_TESTNET=false — status snapshot is intended for "
            "testnet; reading anyway but treat numbers with caution.",
            file=sys.stderr,
        )

    broker = BinanceLiveBroker(
        allowed_pairs=set(settings.pairs),
        testnet=settings.binance_testnet,
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
    )

    journal_rows = _read_journal()
    status = build_status(
        broker=broker,
        pairs=list(settings.pairs),
        diagnose_mod=diagnose_mod,
        journal_rows=journal_rows,
    )

    if args.json:
        json.dump(status, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_human(status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
