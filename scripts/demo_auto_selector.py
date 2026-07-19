#!/usr/bin/env python3
"""
DEMO + SMOKE TEST — prove the strategy auto-selector actually FIRES A SWITCH.

The live auto-selector (`ictbot.runtime.strategy_evaluator`) has only ever decided STAY, because no
arm has accrued an *unseen, deployed* forward track yet (correct anti-chasing). So there's no live
evidence the engine CAN switch. This harness supplies that evidence end-to-end:

  - It runs the REAL `strategy_evaluator.run()` (real `contest_readiness.run_readiness` +
    `recommend_arm`, real hysteresis, real state/recommendation/SIM writes) over SEEDED synthetic
    on-disk data in a THROWAWAY isolated data dir (`ALLOCATOR_DATA_DIR`) — nothing mocked in the
    decision path, and the live `data/` tree is never touched.
  - Round 1: the challenger qualifies but it's the FIRST eval → `consecutive 1/2` → **STAY** (proves
    the engine won't chase a one-off spike).
  - Round 2: the challenger persists → `consecutive 2/2` + beats the incumbent by ≥ margin → **SWITCH**.
    The SIM book is driven (`strategy_select.json`) and the live recommendation flips with an `apply_cmd`.
  - Round 3 (opt-in, SIMULATED): with `STRATEGY_AUTO_APPLY_LIVE` on, shows `apply_live` would rewrite
    `STRATEGY_NAME` — with `rewrite_env_key` stubbed so NO real `.env` is ever touched.

Exits non-zero if any expected transition fails, so it doubles as a smoke test.

Run:  bash scripts/demo_auto_selector.sh        (or)  python scripts/demo_auto_selector.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- bootstrap: make `ictbot`/`scripts` importable AND isolate the data dir BEFORE any ictbot import.
_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO / "src"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CHALLENGER = "dual_momentum"   # a real registered arm that recommend_arm may promote (≠ momentum_adaptive)
INCUMBENT = "mean_reversion"   # the simulated current live arm the switch would replace


def _seed(data_dir: Path) -> None:
    """Seed the isolated tree so REAL run_readiness/recommend_arm nominate CHALLENGER:
    survival ✅ (verdicts) + stability ROBUST (grades) + a forward-eligible, *deployed* isolated track."""
    reports = data_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    # verdicts (strategy_gates.json): survival gate per arm; incumbent also needs `perf` for its score.
    (reports / "strategy_gates.json").write_text(json.dumps({
        CHALLENGER: {"survival": {"passed": True, "worst_week_dd": 0.10, "trades_per_week": 14.0}},
        INCUMBENT: {"survival": {"passed": True, "worst_week_dd": 0.15},
                    "perf": {"total_return": -0.10}},
    }, indent=2) + "\n", encoding="utf-8")

    # stability (strategy_stability.json): recommend_arm requires the challenger be ROBUST (strict).
    (reports / "strategy_stability.json").write_text(json.dumps({
        CHALLENGER: {"grade": "ROBUST"}, INCUMBENT: {"grade": "ROBUST"},
    }, indent=2) + "\n", encoding="utf-8")

    # Isolated forward track for the challenger — the ONLY way `deploy.deployed=True` (the non-vacuous
    # gate; the campaign path returns deploy=None → never promotable). 12 REBALANCE rows over 6 days
    # (≥10 rows, span ≥ 5d), monotonically rising NAV (worst-7d-DD 0), n_swaps=1 each (≈14 trades/wk).
    jdir = data_dir / "forward" / CHALLENGER / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    n = 12
    rows = []
    for i in range(n):
        ts = now - timedelta(days=6) + timedelta(days=6 * i / (n - 1))
        nav = 100.0 + 6.0 * i / (n - 1)  # 100.00 → 106.00, strictly increasing
        rows.append({"event": "REBALANCE", "strategy": CHALLENGER, "ts": ts.isoformat(),
                     "nav_after": round(nav, 4), "n_swaps": 1, "deploy_cap": 0.5})
    (jdir / "allocator_journal.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _row(label: str, d: dict) -> str:
    h = d["hysteresis"]
    return (f"  {label:<8} {d['action']:<7} champion={str(d['champion']):<14} "
            f"recommended={str(d['recommended']):<14} consec={h['consecutive']}/{h['sustain']} "
            f"sim={d.get('sim_applied')} live={d.get('live_applied')}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bnb_demo_autoselect_"))
    os.environ["ALLOCATOR_DATA_DIR"] = str(tmp)          # MUST precede the ictbot import below

    # Imports AFTER the env var is set — DATA_DIR/JOURNAL_DIR resolve at import time.
    import ictbot.runtime.kill_switch as ks
    from ictbot.runtime import strategy_evaluator as se
    from ictbot.runtime import strategy_select

    _seed(tmp)
    se._live_arm = lambda: INCUMBENT   # deterministic incumbent (no live journal in the isolated tree)

    print("=" * 96)
    print("AUTO-SELECTOR DEMO — proving a real STAY → SWITCH (isolated; live data untouched)")
    print("=" * 96)
    print(f"  isolated ALLOCATOR_DATA_DIR : {tmp}")
    print(f"  STATE_FILE                  : {se.STATE_FILE}")
    print(f"  RECO_FILE                   : {se.RECO_FILE}")
    print(f"  STRATEGY_SELECT_FILE (SIM)  : {strategy_select.STRATEGY_SELECT_FILE}")
    print(f"  incumbent (live arm)        : {INCUMBENT}")
    print(f"  challenger (seeded ready)   : {CHALLENGER}")
    h0 = se.evaluate()["hysteresis"]
    print(f"  thresholds                  : margin {h0['margin']} · sustain {h0['sustain']} evals\n")

    # ---- Round 1 — challenger qualifies, but first eval → STAY (won't chase a spike) ----
    d1 = se.run()
    print(_row("round-1", d1))
    assert d1["champion"] == CHALLENGER, f"expected champion={CHALLENGER}, got {d1['champion']} (seeding?)"
    assert d1["action"] == "STAY", f"round-1 should STAY (sustain not met), got {d1['action']}"
    assert d1["hysteresis"]["consecutive"] == 1, d1["hysteresis"]
    assert not strategy_select.STRATEGY_SELECT_FILE.exists(), "SIM must NOT be driven before the switch"

    # ---- Round 2 — challenger persists → consecutive hits sustain + beats margin → SWITCH ----
    d2 = se.run()
    print(_row("round-2", d2))
    assert d2["action"] == "SWITCH", f"round-2 should SWITCH, got {d2['action']}: {d2['reason']}"
    assert d2["recommended"] == CHALLENGER and d2["switch"] is True
    assert d2["hysteresis"]["consecutive"] == 2, d2["hysteresis"]
    assert d2["sim_applied"] == CHALLENGER, f"SIM book should be driven to {CHALLENGER}"

    # Artifacts the engine wrote (all in the isolated tree):
    sim = json.loads(strategy_select.STRATEGY_SELECT_FILE.read_text(encoding="utf-8"))
    assert sim["strategy"] == CHALLENGER, sim
    reco = json.loads(se.RECO_FILE.read_text(encoding="utf-8"))
    assert reco["switch"] is True and (reco["apply_cmd"] or "").endswith(CHALLENGER), reco
    state = json.loads(se.STATE_FILE.read_text(encoding="utf-8"))
    assert state["last_champion"] == CHALLENGER and state["consecutive"] == 2, state
    assert len(state["history"]) == 2, state

    # ---- Round 3 — opt-in live auto-apply, SIMULATED (rewrite_env_key stubbed → no real .env touched) ----
    captured: list[tuple] = []
    ks.rewrite_env_key = lambda k, v: captured.append((k, v))
    se.settings.strategy_auto_apply_live = True
    d3 = se.run()
    print(_row("round-3", d3))
    assert d3["live_applied"] == CHALLENGER, f"opt-in live-apply should report {CHALLENGER}"
    assert captured == [("STRATEGY_NAME", CHALLENGER)], f"expected one env rewrite, got {captured}"

    print("\n" + "-" * 96)
    print("WHAT THIS PROVES")
    print("-" * 96)
    print(
        "  • The 'switching engine' is not inert: given a challenger that beats the incumbent on an\n"
        "    UNSEEN, DEPLOYED forward track, it fires a real SWITCH — driving the SIM book and writing a\n"
        "    live recommendation with an operator apply_cmd.\n"
        "  • Anti-chasing holds: the SWITCH needs the edge SUSTAINED for `sustain` consecutive evals\n"
        "    (round-1 STAY → round-2 SWITCH) AND a score margin ≥ `margin` — a one-off spike won't flip it.\n"
        "  • Contest-safe: LIVE stays recommend-only by default; the real arm changes only with the\n"
        "    opt-in (round-3) or operator sign-off. This demo never touched the live data/ tree or .env.")
    print(f"\n✅ DEMO PASSED — artifacts in throwaway tmp: {tmp}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"\n❌ DEMO FAILED: {e}", file=sys.stderr)
        sys.exit(1)
