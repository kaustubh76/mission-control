#!/usr/bin/env python3
"""
Sim-test every registered strategy — prove each arm reads/writes correctly in SIM.

For each real arm (registry.real_arms()) this VALIDATES its isolated SIM track
(data/forward/<arm>/, produced by `make forward_track_all`): every REBALANCE row carries the
required schema, NAV is positive, weights never exceed 1, every target token is in the universe,
n_swaps matches the tx list, and the persisted state ledger round-trips (cumulative_swaps is
monotonic, balances is a dict). It also surfaces the DISTINCT TOKENS each arm trades — breakout/grid
touch the whole universe, the momentum family touches 2 (top_k=2, by design).

The `make sim_test_all` target ticks all arms fresh (`forward_track_all`) and then validates; this
script alone is validate-only (offline read of the journals). The core `validate_arm` is pure and
unit-tested. READ-ONLY against the live world — it only reads the isolated SIM journals + writes a
report.

Usage:
  make sim_test_all                                  # tick every arm fresh, then validate
  PYTHONPATH=src:. python scripts/sim_test_all.py     # validate the existing tracks only
  PYTHONPATH=src:. python scripts/sim_test_all.py --no-save
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ictbot.settings import DATA_DIR
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS
from scripts.strategy_campaign import real_arms

REPORT_PATH = DATA_DIR / "reports" / "sim_test_all.md"
REQUIRED = ("ts", "event", "mode", "strategy", "nav_before", "nav_after", "deploy_cap",
            "target", "weights_after", "n_swaps", "tx")


def _read_json_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _load_state(sp: Path) -> dict | None:
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_arm(arm: str, *, data_dir: Path = DATA_DIR) -> tuple[list[dict], dict | None]:
    """(journal rows, state) for an arm: its isolated SIM tree (data/forward/<arm>/) when present,
    else a fallback to the PRODUCTION journal (data/journal/) — so the incumbent `momentum_adaptive`
    is validated against its real SIM track even though it has no isolated forward tree."""
    jdir = data_dir / "forward" / arm / "journal"
    rows = _read_json_lines(jdir / "allocator_journal.jsonl")
    if rows:
        return rows, _load_state(jdir / "allocator_state.json")
    pjdir = data_dir / "journal"   # fallback: the production SIM track
    return _read_json_lines(pjdir / "allocator_journal.jsonl"), _load_state(pjdir / "allocator_state.json")


def validate_arm(rows: list[dict], state: dict | None, arm: str) -> dict:
    """Validate one arm's SIM journal + state. Pure. Returns stats + errors + status.

    A cash-only arm (deploy_cap≈0 → target={} → 0 swaps) is VALID (it records the risk-off regime,
    not a failure). status: OK (valid rows), ERROR (a malformed row / state mismatch), EMPTY (no
    REBALANCE rows yet — run `make forward_track_all`)."""
    reb = [r for r in rows if r.get("event") == "REBALANCE" and (r.get("strategy") or "") == arm]
    errors: list[str] = []
    distinct: set[str] = set()
    total_swaps, navs = 0, []
    for i, r in enumerate(reb):
        missing = [k for k in REQUIRED if k not in r]
        if missing:
            errors.append(f"row {i}: missing {missing}")
        nav = r.get("nav_after")
        if not isinstance(nav, (int, float)) or nav <= 0:
            errors.append(f"row {i}: bad nav_after {nav!r}")
        else:
            navs.append(float(nav))
        tgt = r.get("target")
        if not isinstance(tgt, dict):
            errors.append(f"row {i}: target is not a dict")
        else:
            bad = [t for t in tgt if t not in CONTEST_TOKENS]
            if bad:
                errors.append(f"row {i}: non-universe tokens {bad}")
            distinct |= {t for t, w in tgt.items() if isinstance(w, (int, float)) and w > 0}
        wa = r.get("weights_after")
        if isinstance(wa, dict) and wa:
            s = sum(v for v in wa.values() if isinstance(v, (int, float)))
            if s > 1.0 + 1e-6:
                errors.append(f"row {i}: weights sum {s:.3f} > 1 (over-deployed)")
        ns = r.get("n_swaps")
        if not isinstance(ns, int) or ns < 0:
            errors.append(f"row {i}: bad n_swaps {ns!r}")
        else:
            total_swaps += ns
            if ns > 0 and not r.get("tx"):
                errors.append(f"row {i}: n_swaps={ns} but empty tx")
    if state is not None and reb:
        last_cum = reb[-1].get("cumulative_swaps")
        st_cum = state.get("cumulative_swaps")
        if isinstance(last_cum, int) and isinstance(st_cum, int) and st_cum < last_cum:
            errors.append(f"state cumulative_swaps {st_cum} < journal {last_cum} (ledger drift)")
        if state.get("balances") is not None and not isinstance(state.get("balances"), dict):
            errors.append("state.balances is not a dict")
    status = "OK" if (reb and not errors) else ("ERROR" if errors else "EMPTY")
    return {"arm": arm, "n_rebalances": len(reb), "total_swaps": total_swaps,
            "distinct_tokens": sorted(distinct), "n_distinct": len(distinct),
            "nav_first": (navs[0] if navs else None), "nav_last": (navs[-1] if navs else None),
            "errors": errors, "status": status}


_STATUS = {"OK": "✅ OK", "ERROR": "❌ ERROR", "EMPTY": "⏳ EMPTY"}


def render_report(results: list[dict], *, now_iso: str) -> str:
    out = [
        "# Sim-test all strategies",
        "",
        f"_Generated by `make sim_test_all` at **{now_iso}**. Validates each arm's isolated SIM "
        "journal (`data/forward/<arm>/`) — schema, NAV, weights ≤ 1, universe-only tokens, n_swaps↔tx, "
        "and the state ledger round-trip. Run `make forward_track_all` to refresh the ticks._",
        "",
        "| Arm | Status | Rebalances | Swaps | Distinct tokens | NAV first→last |",
        "|---|:--:|--:|--:|---|---|",
    ]
    for r in sorted(results, key=lambda r: (r["status"] != "OK", -r["n_distinct"], r["arm"])):
        toks = ", ".join(r["distinct_tokens"]) or "— (cash)"
        nav = f"{r['nav_first']:.2f}→{r['nav_last']:.2f}" if r["nav_first"] is not None else "—"
        out.append(f"| `{r['arm']}` | {_STATUS[r['status']]} | {r['n_rebalances']} | "
                   f"{r['total_swaps']} | {toks} | {nav} |")
    errs = [r for r in results if r["errors"]]
    if errs:
        out += ["", "## Errors", ""]
        out += [f"- **{r['arm']}**: " + "; ".join(r["errors"][:5]) for r in errs]
    out += [
        "",
        "_OK = valid journal + state round-trip · EMPTY = no ticks yet (run `make forward_track_all`). "
        "Distinct tokens shows breadth: breakout/grid touch the universe, the momentum family touches 2 "
        "(`top_k=2`, by design — the contest ≥1-trade/day floor rotates the rest, see `trade_floor_rotate`)._",
        "",
    ]
    return "\n".join(out)


def run_sim_test_all(*, arms: list[str] | None = None, save: bool = True,
                     report_path: Path = REPORT_PATH, now_iso: str | None = None,
                     data_dir: Path = DATA_DIR) -> list[dict]:
    now_iso = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    arms = arms or real_arms()
    results = []
    for arm in arms:
        rows, state = read_arm(arm, data_dir=data_dir)
        results.append(validate_arm(rows, state, arm))
    if save:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(results, now_iso=now_iso), encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate every registered strategy's SIM journal + state.")
    ap.add_argument("--no-save", action="store_true", help="print only; don't write the report")
    args = ap.parse_args()

    results = run_sim_test_all(save=not args.no_save)
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_err = sum(1 for r in results if r["status"] == "ERROR")
    print(f"sim-test all strategies — {len(results)} arms ({n_ok} OK, {n_err} ERROR, "
          f"{len(results) - n_ok - n_err} empty)\n")
    print(f"{'arm':22} {'status':9} {'reb':>4} {'swaps':>6} {'distinct':>8}  tokens")
    print("-" * 78)
    for r in sorted(results, key=lambda r: (r["status"] != "OK", -r["n_distinct"], r["arm"])):
        toks = ", ".join(r["distinct_tokens"]) or "— (cash)"
        print(f"{r['arm']:22} {r['status']:9} {r['n_rebalances']:>4} {r['total_swaps']:>6} "
              f"{r['n_distinct']:>8}  {toks}")
    for r in results:
        for e in r["errors"][:3]:
            print(f"  ⚠️  {r['arm']}: {e}")
    if not args.no_save:
        print(f"\nwrote: {REPORT_PATH}")
    print("\nREAD-ONLY — validates the isolated SIM journals; no arm changed, nothing live touched.")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
