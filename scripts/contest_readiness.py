#!/usr/bin/env python3
"""
Contest-readiness rollup — the capstone that fuses the three automated signals into ONE verdict.

The campaign produced three separate signals per arm; this answers the actual question — "is this arm
contest-ready?" — by combining them:

  - stability  (make stability)        : ROBUST / FRAGILE / UNSTABLE   (verdict trustworthiness)
  - survival   (make campaign)         : DQ-safe < 25% DD AND >= 7 t/wk (backtest gate)
  - forward    (isolated track / campaign) : worst-7d DD <25% · >=7 t/wk · median wk ret >= 0

Forward prefers the ISOLATED per-arm track (data/forward/<arm>/, seeded by `make forward_track`) when it
exists — that's the real, independently-accruing evidence — else the production forward verdict.

Readiness (NEVER auto-promotes — the final step is always a human):
  ✅ READY (sign-off)  survival ✅ AND stability in {ROBUST,FRAGILE} AND forward eligible
  ⏳ IN PROGRESS       survival ✅ + stability ok, forward not yet eligible (still accruing)
  ❌ NOT READY         survival ❌ OR stability UNSTABLE (the blocking gate is named)
  🔒 INCUMBENT         momentum_adaptive — the locked contest default (reference)

READ-ONLY: reads the verdict/grade JSON + the isolated journals, writes ONE report. It never persists a
verdict, never ticks, never changes an arm. Promotion stays operator sign-off (STRATEGY_NAME + ENABLE_LIVE_TRADING).

Usage:
  python scripts/contest_readiness.py                 # rollup all arms, write the report
  python scripts/contest_readiness.py --no-save        # print only
  python scripts/contest_readiness.py --forward-min-days 14   # rigorous forward window
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import scripts.forward_promote as fp
from ictbot.runtime import stability_grades, verdicts
from ictbot.settings import DATA_DIR
from scripts.strategy_campaign import real_arms

REPORT_PATH = DATA_DIR / "reports" / "contest_readiness.md"
DEFAULT_FORWARD_MIN_DAYS = 5.0
INCUMBENT = "momentum_adaptive"          # the locked contest default

READY = "✅ READY (sign-off)"
IN_PROGRESS = "⏳ IN PROGRESS"
NOT_READY = "❌ NOT READY"
INCUMBENT_TAG = "🔒 INCUMBENT"
_RANK = {INCUMBENT_TAG: 0, READY: 1, IN_PROGRESS: 2, NOT_READY: 3}


def _read_isolated_rows(arm: str) -> list[dict]:
    """Parsed REBALANCE-and-other rows from this arm's ISOLATED track (data/forward/<arm>/), or []."""
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


def _isolated_forward(arm: str, min_days: float) -> dict | None:
    """The forward verdict from this arm's ISOLATED track, or None if it has no track yet."""
    rows = _read_isolated_rows(arm)
    return fp._verdict_for(rows, arm, min_days=min_days) if rows else None


def deploy_summary(rows: list[dict], arm: str) -> dict:
    """Did this isolated forward track DEPLOY capital, or is it cash-vacuous? Pure over raw rows.

    A risk-off regime pins deploy_cap=0 → the book sits 100% cash → NAV is flat → the track records
    the REGIME, not the strategy's allocation. This labels that honestly; it does NOT touch the
    eligibility gate (that already rejects cash tracks via the ≥7 trades/wk floor).

    Returns {n_rows, deployed_fraction, mean_deploy_cap, deployed}: deployed_fraction = share of this
    arm's REBALANCE rows that moved capital (n_swaps>0 OR deploy_cap>0); deployed = fraction > 0.
    """
    reb = [r for r in rows if r.get("event") == "REBALANCE" and (r.get("strategy") or "") == arm]
    n = len(reb)
    if n == 0:
        return {"n_rows": 0, "deployed_fraction": 0.0, "mean_deploy_cap": None, "deployed": False}
    caps = [float(r["deploy_cap"]) for r in reb if isinstance(r.get("deploy_cap"), (int, float))]

    def _moved(r: dict) -> bool:
        dc = r.get("deploy_cap")
        return (r.get("n_swaps") or 0) > 0 or (isinstance(dc, (int, float)) and dc > 0)

    frac = sum(1 for r in reb if _moved(r)) / n
    return {"n_rows": n, "deployed_fraction": frac,
            "mean_deploy_cap": (sum(caps) / len(caps) if caps else None), "deployed": frac > 0}


def _readiness(arm: str, stability: dict | None, survival: dict | None, forward: dict | None) -> tuple[str, str]:
    if arm == INCUMBENT:
        return INCUMBENT_TAG, "locked contest default"
    sv_pass = bool(survival and survival.get("passed"))
    grade = (stability or {}).get("grade")
    if not sv_pass:
        return NOT_READY, "survival ❌"
    if grade == "UNSTABLE":
        return NOT_READY, "stability UNSTABLE"
    if forward and forward.get("forward_eligible"):
        return READY, "all gates cleared"
    fwd = "forward ❌ not yet" if (forward or {}).get("status") == "evaluated" else "forward accruing"
    return IN_PROGRESS, fwd


def evaluate_arm(arm: str, *, vmap: dict, gmap: dict, forward_min_days: float) -> dict:
    stability = gmap.get(arm)
    survival = (vmap.get(arm) or {}).get("survival")
    iso_rows = _read_isolated_rows(arm)
    if iso_rows:
        forward = fp._verdict_for(iso_rows, arm, min_days=forward_min_days)
        forward_src, deploy = "isolated", deploy_summary(iso_rows, arm)
    else:
        forward = (vmap.get(arm) or {}).get("forward")
        forward_src, deploy = "campaign", None
    verdict, blocker = _readiness(arm, stability, survival, forward)
    return {"arm": arm, "stability": stability, "survival": survival, "forward": forward,
            "forward_src": forward_src, "deploy": deploy, "verdict": verdict, "blocker": blocker}


def _key(r: dict):
    dd = ((r.get("survival") or {}).get("worst_week_dd"))
    return (_RANK.get(r["verdict"], 4), dd if dd is not None else 1.0, r["arm"])


def recommend_arm(results: list[dict], *, incumbent: str = INCUMBENT) -> dict:
    """Risk-first contest-arm recommendation over the already-ranked readiness results (read-only,
    NEVER promotes). A promotion candidate must STRICTLY clear every gate: verdict READY (survival ✅
    + stability not UNSTABLE + forward eligible) AND stability grade **ROBUST** (stricter than READY's
    {ROBUST,FRAGILE} floor) AND a **non-vacuous** forward track (it actually deployed capital — a
    cash-only track is no evidence) AND not the incumbent. `results` is already ordered risk-first
    (READY arms by lowest worst-week DD), so the first qualifier wins; PnL/win-rate is a documented
    tiebreak only. Returns {action, arm, line, reason}; the contest flip stays operator sign-off.
    """
    for r in results:
        if r["arm"] == incumbent or r["verdict"] != READY:
            continue
        if (r.get("stability") or {}).get("grade") != "ROBUST":
            continue
        dep = r.get("deploy")
        if not (dep and dep.get("deployed")):           # cash-vacuous / no isolated deployment evidence
            continue
        dd = (r.get("survival") or {}).get("worst_week_dd")
        ddtxt = f"{dd * 100:.1f}%" if dd is not None else "n/a"
        line = (f"PROMOTE-CANDIDATE: {r['arm']} — READY, ROBUST, forward-eligible on a deployed "
                f"track, worst-week DD {ddtxt} (lowest-DD among candidates). Operator sign-off required.")
        return {"action": "PROMOTE-CANDIDATE", "arm": r["arm"], "line": line,
                "reason": "survival ✅ + stability ROBUST + non-vacuous forward eligibility"}
    line = ("STAY INCUMBENT — no challenger has cleared all gates (survival ✅ + stability ROBUST + "
            "forward-eligible on a deployed, non-cash track).")
    return {"action": "STAY", "arm": None, "line": line, "reason": "no qualifying challenger"}


def run_readiness(*, arms: list[str] | None = None, forward_min_days: float = DEFAULT_FORWARD_MIN_DAYS,
                  save: bool = True, report_path: Path = REPORT_PATH, now_iso: str | None = None,
                  vmap: dict | None = None, gmap: dict | None = None) -> list[dict]:
    now_iso = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    vmap = verdicts.load() if vmap is None else vmap
    gmap = stability_grades.load() if gmap is None else gmap
    arms = arms or real_arms()
    results = sorted(
        (evaluate_arm(a, vmap=vmap, gmap=gmap, forward_min_days=forward_min_days) for a in arms),
        key=_key)
    if save:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report(results, forward_min_days=forward_min_days, now_iso=now_iso),
                               encoding="utf-8")
    return results


def _grade_cell(s: dict | None) -> str:
    return (s or {}).get("grade") or "—"


def _survival_cell(s: dict | None) -> str:
    if not s or s.get("passed") is None:
        return "—"
    dd = s.get("worst_week_dd")
    tag = "✅" if s["passed"] else "❌"
    return f"{tag} {dd * 100:.1f}% DD" if dd is not None else tag


def _forward_cell(f: dict | None, src: str, deploy: dict | None = None) -> str:
    if not f or f.get("status") is None:
        return "—"
    if f.get("status") != "evaluated":
        # Honest accruing labels: distinguish a cash-vacuous track (sat in cash, deploy_cap≈0 →
        # records the regime, not the strategy) from one that's merely young.
        if deploy and deploy.get("n_rows", 0) > 0 and not deploy.get("deployed"):
            return f"⏳ accruing (cash — deploy_cap≈0, {src})"
        n = (deploy or {}).get("n_rows")
        return f"⏳ accruing ({n} rows, {src})" if n else f"⏳ insufficient ({src})"
    return (f"✅ eligible ({src})" if f.get("forward_eligible") else f"❌ not yet ({src})")


def render_report(results: list[dict], *, forward_min_days: float, now_iso: str,
                  recommendation: dict | None = None) -> str:
    rec = recommendation or recommend_arm(results)
    out = [
        "# Contest-readiness rollup",
        "",
        f"_Generated by `make readiness` at **{now_iso}**. Fuses stability + survival + forward into "
        f"one verdict per arm. Forward window **{forward_min_days:g}d**; forward prefers the isolated "
        "per-arm track (`data/forward/<arm>/`) when present, else the campaign verdict._",
        "",
        "**No auto-promotion.** READY means every automated gate is cleared — the contest arm is still "
        "chosen by an operator (`STRATEGY_NAME` + `ENABLE_LIVE_TRADING`). No long-only edge is claimed "
        "(decision-record §1); this reports gates cleared, not alpha.",
        "",
        f"**Recommendation:** {rec['line']}",
        "",
        "| Arm | Readiness | Stability | Survival | Forward | Blocking gate |",
        "|---|:--:|:--:|:--:|:--:|---|",
    ]
    for r in results:
        out.append(
            f"| `{r['arm']}` | {r['verdict']} | {_grade_cell(r['stability'])} | "
            f"{_survival_cell(r['survival'])} | "
            f"{_forward_cell(r['forward'], r['forward_src'], r.get('deploy'))} | "
            f"{r['blocker']} |"
        )
    out += [
        "",
        "_Readiness — **READY**: survival ✅ + stability ∈ {ROBUST,FRAGILE} + forward eligible (pending "
        "sign-off). **IN PROGRESS**: survival + stability ok, forward still accruing. **NOT READY**: "
        "survival ❌ or stability UNSTABLE. **INCUMBENT**: the locked default. Run `make stability` + "
        "`make campaign` to refresh inputs; `make forward_track_all` to accrue forward evidence._",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fuse stability + survival + forward into a readiness verdict.")
    ap.add_argument("--forward-min-days", type=float, default=DEFAULT_FORWARD_MIN_DAYS)
    ap.add_argument("--no-save", action="store_true", help="print only; don't write the report")
    args = ap.parse_args()

    results = run_readiness(forward_min_days=args.forward_min_days, save=not args.no_save)
    print(f"contest-readiness rollup — {len(results)} arms, forward window {args.forward_min_days:g}d\n")
    print(f"{'arm':22} {'readiness':20} {'stability':10} {'survival':16} {'blocking gate':20}")
    print("-" * 92)
    for r in results:
        print(f"{r['arm']:22} {r['verdict']:20} {_grade_cell(r['stability']):10} "
              f"{_survival_cell(r['survival']):16} {r['blocker']:20}")
    print(f"\n>>> {recommend_arm(results)['line']}")
    if not args.no_save:
        print(f"\nwrote: {REPORT_PATH}")
    print("\nREAD-ONLY — no verdict persisted, no arm changed; promotion stays operator sign-off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
