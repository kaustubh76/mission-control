#!/usr/bin/env python3
"""
Playbook ↔ implementation wiring — prove every Top-10 family in docs/strategy_playbook.md is an
actually-registered, tested, and VALIDATED arm, and splice the status matrix into the doc's §11 so
the research narrative can never silently drift from the code.

`PLAYBOOK_FAMILIES` is the canonical map from the playbook's ranked Top-10 families (§3) to the
registered arm(s) that implement each. It is MANY-TO-MANY on purpose: families #1/#2/#7
(trend-pullback / TSMOM / MA-cross) collapse onto the momentum + MA-filter arms — their ICT entry
was dropped (§6) — while Donchian (#4/#9) both land on `breakout`. The parity guarantee
(tests/test_playbook_parity.py) is COVERAGE, not a bijection: every real registered arm appears in
≥1 family, and every arm named in the map is registered. So a new arm added to the registry without
a playbook home (or a typo'd arm name here) fails CI.

The matrix renders ONE ROW PER ARM (the direct answer to "is every strategy built + validated"):
its playbook lineage + survival GATE + stability grade + forward verdict + the PnL/win-rate
SCOREBOARD (backtest total-return + window win-rate, plus the live day win-rate from its forward
track). Survival is the gate; PnL/win-rate is the scoreboard — no edge is claimed (decision-record §1).

READ-ONLY: reads the persisted verdicts/grades JSON + the journals; writes ONLY between the §11
markers. Never ticks, never changes an arm. Run `make campaign` + `make stability` first to populate.

Usage:
  python scripts/playbook_status.py                 # splice the §11 matrix into the playbook
  python scripts/playbook_status.py --no-save        # print only; don't rewrite the doc
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import scripts.forward_promote as fp
from ictbot.api.reads import read_journal
from ictbot.runtime import performance, stability_grades, verdicts
from ictbot.settings import DATA_DIR
from scripts.strategy_campaign import real_arms

PLAYBOOK_START = "<!-- PLAYBOOK:START -->"
PLAYBOOK_END = "<!-- PLAYBOOK:END -->"

_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = _ROOT / "docs" / "strategy_playbook.md"
DEFAULT_FORWARD_MIN_DAYS = 5.0

# The playbook's ranked Top-10 families (docs/strategy_playbook.md §3) → the registered arm(s) that
# implement each. Many-to-many; keep `num` 1-10 unique. Edit HERE when an arm's playbook lineage
# changes — the parity test pins this to the registry.
PLAYBOOK_FAMILIES: list[dict] = [
    {"num": 1, "name": "Trend-Following Pullback", "arms": ["momentum_adaptive", "momentum_cmc", "momentum_mafilter"]},
    {"num": 2, "name": "Time-Series Momentum / MA Trend", "arms": ["momentum", "momentum_fast", "momentum_mafilter"]},
    {"num": 3, "name": "Dual Momentum (cash-out)", "arms": ["dual_momentum"]},
    {"num": 4, "name": "Volatility Breakout (Keltner/Donchian ORB)", "arms": ["breakout"]},
    {"num": 5, "name": "Grid / Range", "arms": ["grid"]},
    {"num": 6, "name": "Short-Horizon Mean Reversion", "arms": ["mean_reversion"]},
    {"num": 7, "name": "MA Crossover (trend filter)", "arms": ["momentum_mafilter"]},
    {"num": 8, "name": "Cross-Sectional Momentum / Rotation", "arms": ["rotation"]},
    {"num": 9, "name": "Donchian / Turtle Breakout", "arms": ["breakout"]},
    {"num": 10, "name": "Volatility-Targeting overlay", "arms": ["momentum_voltarget"]},
]


def mapped_arms() -> set[str]:
    return {a for fam in PLAYBOOK_FAMILIES for a in fam["arms"]}


def families_for(arm: str) -> list[int]:
    """Sorted playbook family numbers that map to `arm` (the inverse of PLAYBOOK_FAMILIES)."""
    return sorted(fam["num"] for fam in PLAYBOOK_FAMILIES if arm in fam["arms"])


def _read_isolated_rows(arm: str) -> list[dict]:
    """Parsed REBALANCE rows from this arm's ISOLATED forward track (data/forward/<arm>/), or []."""
    jpath = DATA_DIR / "forward" / arm / "journal" / "allocator_journal.jsonl"
    if not jpath.exists():
        return []
    rows = []
    for line in jpath.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _forward_for(arm: str, prod_rows: list[dict], min_days: float) -> tuple[dict, dict, str]:
    """(verdict, day-perf, source) — prefer the isolated per-arm track, else the production journal.

    Mirrors contest_readiness's prefer-isolated-else-campaign rule so the playbook agrees with the
    readiness rollup. `perf` is the live DAY win-rate / net PnL from the same NAV track."""
    iso = _read_isolated_rows(arm)
    rows, src = (iso, "isolated") if iso else (prod_rows, "prod")
    verdict = fp._verdict_for(rows, arm, min_days=min_days)
    curve = [(dt.isoformat(), nav) for dt, nav, _ in fp._strategy_rows(rows, arm)]
    return verdict, performance.forward_perf(curve), src


def evaluate_arm(arm: str, *, vmap: dict, gmap: dict, prod_rows: list[dict],
                 min_days: float) -> dict:
    v = vmap.get(arm) or {}
    fwd_verdict, fwd_perf, fwd_src = _forward_for(arm, prod_rows, min_days)
    return {
        "arm": arm,
        "families": families_for(arm),
        "registered": arm in real_arms(),
        "survival": v.get("survival"),
        "stability": gmap.get(arm),
        "perf": v.get("perf"),             # backtest PnL + WINDOW win-rate (persisted by campaign)
        "forward": fwd_verdict,
        "fwd_perf": fwd_perf,              # live DAY win-rate + net PnL
        "fwd_src": fwd_src,
    }


# ── cells ────────────────────────────────────────────────────────────────────────────────────
def _fam_cell(nums: list[int]) -> str:
    return ", ".join(f"#{n}" for n in nums) if nums else "⚠️ unmapped"


def _survival_cell(s: dict | None) -> str:
    if not s or s.get("passed") is None:
        return "—"
    dd = s.get("worst_week_dd")
    tag = "✅" if s["passed"] else "❌"
    return f"{tag} {dd * 100:.1f}% DD" if dd is not None else tag


def _grade_cell(s: dict | None) -> str:
    return (s or {}).get("grade") or "—"


def _forward_cell(f: dict | None, src: str) -> str:
    if not f or f.get("status") is None:
        return "—"
    if f.get("status") != "evaluated":
        return f"⏳ {src}"
    return (f"✅ eligible ({src})" if f.get("forward_eligible") else f"❌ not yet ({src})")


def _pct(x, places: int = 1, *, signed: bool = False) -> str:
    return "—" if x is None else f"{x * 100:{'+' if signed else ''}.{places}f}%"


def _fwd_winrate_cell(fp_perf: dict | None) -> str:
    if not fp_perf or fp_perf.get("status") in (None, "none"):
        return "—"
    if fp_perf.get("status") != "evaluated" or fp_perf.get("win_rate") is None:
        return "⏳ accruing"
    return f"{fp_perf['win_rate'] * 100:.0f}% ({fp_perf['wins']}/{fp_perf['decided']}d)"


def render_matrix(results: list[dict], *, now_iso: str) -> str:
    """The full §11 block (inclusive of markers) — ordered by playbook rank (min family number)."""
    ranked = sorted(results, key=lambda r: (min(r["families"]) if r["families"] else 99, r["arm"]))
    n_arms, n_fam = len(results), len(PLAYBOOK_FAMILIES)
    lines = [
        PLAYBOOK_START,
        "",
        f"_Auto-generated by `make playbook` — do not hand-edit between the markers. All **{n_fam}** "
        f"playbook families map to **{n_arms}** registered arms (parity in `tests/test_playbook_parity.py`). "
        f"Survival = GATE; PnL/win-rate = SCOREBOARD (no edge claim). Forward window "
        f"**{DEFAULT_FORWARD_MIN_DAYS:g}d**. Last run **{now_iso}**._",
        "",
        "| Arm | Family | Survival | Stability | Forward | Backtest total-ret | Win-rate (window) | "
        "Forward win-rate (day) |",
        "|---|---|:--:|:--:|:--:|--:|--:|--:|",
    ]
    for r in ranked:
        perf = r.get("perf") or {}
        lines.append(
            f"| `{r['arm']}` | {_fam_cell(r['families'])} | {_survival_cell(r['survival'])} | "
            f"{_grade_cell(r['stability'])} | {_forward_cell(r['forward'], r['fwd_src'])} | "
            f"{_pct(perf.get('total_return'), 1, signed=True)} | {_pct(perf.get('win_rate'), 0)} | "
            f"{_fwd_winrate_cell(r.get('fwd_perf'))} |"
        )
    lines += [
        "",
        "**Win-rate (window)** = backtest share of rolling 7-day windows positive (`pct_up`). "
        "**Win-rate (day)** = live up-days / decided-days from the arm's forward track (matches the "
        "dashboard `pnl.ts`); ⏳ accruing until the wall-clock track has resolved days. Backtest "
        "total-return is a scoreboard over a long trending sample — **regime luck, not edge** "
        "([bnb_strategy_decision.md](bnb_strategy_decision.md) §1). Refresh inputs: `make campaign` "
        "(survival + scoreboard), `make stability` (grades), `make forward_track_all` (forward).",
        "",
        PLAYBOOK_END,
    ]
    return "\n".join(lines)


def splice_playbook(doc_text: str, block: str) -> str:
    """Replace the §11 PLAYBOOK block (inclusive of markers) with `block`; append if absent.

    count=1 + a lambda replacement: only the first marker pair is rewritten, and the block is
    inserted verbatim (no regex backref interpretation) — same contract as the guardian splice."""
    pat = re.compile(re.escape(PLAYBOOK_START) + r".*?" + re.escape(PLAYBOOK_END), re.DOTALL)
    if pat.search(doc_text):
        return pat.sub(lambda _m: block, doc_text, count=1)
    return doc_text.rstrip() + "\n\n" + block + "\n"


def run_playbook(*, save: bool = True, doc_path: Path = DOC_PATH, now_iso: str | None = None,
                 vmap: dict | None = None, gmap: dict | None = None,
                 prod_rows: list[dict] | None = None,
                 forward_min_days: float = DEFAULT_FORWARD_MIN_DAYS) -> list[dict]:
    now_iso = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    vmap = verdicts.load() if vmap is None else vmap
    gmap = stability_grades.load() if gmap is None else gmap
    prod_rows = read_journal(limit=5000) if prod_rows is None else prod_rows
    results = [evaluate_arm(a, vmap=vmap, gmap=gmap, prod_rows=prod_rows,
                            min_days=forward_min_days) for a in real_arms()]
    if save and doc_path.exists():
        block = render_matrix(results, now_iso=now_iso)
        doc_path.write_text(splice_playbook(doc_path.read_text(encoding="utf-8"), block),
                            encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Wire the playbook Top-10 families to the registered, "
                                             "validated arms and splice the §11 status matrix.")
    ap.add_argument("--forward-min-days", type=float, default=DEFAULT_FORWARD_MIN_DAYS)
    ap.add_argument("--no-save", action="store_true", help="print only; don't rewrite the playbook")
    args = ap.parse_args()

    results = run_playbook(save=not args.no_save, forward_min_days=args.forward_min_days)
    ranked = sorted(results, key=lambda r: (min(r["families"]) if r["families"] else 99, r["arm"]))
    print(f"playbook ↔ implementation status — {len(results)} arms, {len(PLAYBOOK_FAMILIES)} families\n")
    print(f"{'arm':22} {'family':12} {'survival':16} {'stability':10} {'bt total-ret':>12}")
    print("-" * 76)
    for r in ranked:
        perf = r.get("perf") or {}
        print(f"{r['arm']:22} {_fam_cell(r['families']):12} {_survival_cell(r['survival']):16} "
              f"{_grade_cell(r['stability']):10} {_pct(perf.get('total_return'), 1, signed=True):>12}")
    if not args.no_save:
        print(f"\nwrote §11 matrix → {DOC_PATH}")
    print("\nREAD-ONLY — survival is the GATE, PnL/win-rate the SCOREBOARD; no edge claimed, no arm changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
