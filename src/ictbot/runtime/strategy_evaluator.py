"""
Forward-gated strategy AUTO-SELECTOR — the honest "switching engine".

Each forward sweep, this scores every registered arm by RISK-ADJUSTED FORWARD performance, picks the
best among DQ-SAFE ones, and applies ANTI-CHASING HYSTERESIS before recommending a switch. It then:
  - drives the SIM track (`strategy_select.save`) so the paper book trades the winner — proving the
    mechanism on real (sim) money, and
  - writes a LIVE RECOMMENDATION (`strategy_evaluation.json`) the operator applies via `live_tick.sh`.

CONTEST SAFETY: this NEVER changes the live/contest arm by itself. `run_allocator` makes the LIVE path
ignore dynamic selection by design; here `apply_live` is gated behind `STRATEGY_AUTO_APPLY_LIVE`
(default False) — recommend-only. The real-money arm changes only on operator sign-off.

REUSE (no reinvention): `contest_readiness.run_readiness`/`recommend_arm` (risk-first readiness +
strictly-gated best challenger), `ab_regime._score` shape (`return − worst_dd`), `runtime.verdicts` /
`stability_grades` / `strategy_select`. This module adds only the decision loop + hysteresis + surfaces.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ictbot.settings import DATA_DIR, settings

STATE_FILE = DATA_DIR / "reports" / "strategy_evaluator_state.json"   # hysteresis memory (consecutive)
RECO_FILE = DATA_DIR / "reports" / "strategy_evaluation.json"          # the LIVE recommendation surface


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"last_champion": None, "consecutive": 0, "history": []}


def _live_arm() -> str:
    """The current live/contest arm (what a switch would change). Journal-aware via reads when present;
    falls back to the configured default. Lazy import so runtime never hard-depends on the API layer."""
    try:
        from ictbot.api import reads

        return reads._live_arm()
    except Exception:
        return settings.strategy_name or "momentum_adaptive"


def _score(r: dict, vmap: dict) -> float | None:
    """Risk-adjusted score for one readiness result (higher = better; DQ-safety is gated separately).
    Forward-eligible arms are scored on their UNSEEN forward track (median weekly ret − worst-7d DD —
    the signal that actually matters); otherwise fall back to backtest perf − survival worst-week DD."""
    fwd = r.get("forward") or {}
    if fwd.get("forward_eligible"):
        mwr, dd = fwd.get("median_weekly_ret"), fwd.get("worst_7d_dd")
        if mwr is not None and dd is not None:
            return float(mwr) - float(dd)
    perf = (vmap.get(r["arm"]) or {}).get("perf") or {}
    sv = r.get("survival") or {}
    tr, wd = perf.get("total_return"), sv.get("worst_week_dd")
    if tr is not None and wd is not None:
        return float(tr) - float(wd)
    if wd is not None:
        return -float(wd)  # only DD known → lower DD is better
    return None


def evaluate(*, forward_min_days: float | None = None, vmap: dict | None = None,
             gmap: dict | None = None, state: dict | None = None) -> dict:
    """Compute the auto-select decision (PURE — reads data + hysteresis state, writes nothing).

    `recommend_arm` already returns the strictly-gated best challenger (READY + stability ROBUST +
    forward-eligible on a non-vacuous track). On top of that we require it to (a) beat the incumbent's
    score by `margin` and (b) hold the champion slot for `sustain` consecutive evals — the anti-chasing
    rails that stop us flipping to whatever recently spiked. Returns the full decision dict.
    """
    # Lazy import: contest_readiness lives under scripts/ (not a package). Ensure the repo root is on
    # sys.path so `scripts.*` resolves regardless of entrypoint (driver, cron, API, tests) — mirrors the
    # Makefile's `PYTHONPATH=src:.` for the readiness/campaign scripts.
    import sys

    from ictbot.settings import PROJECT_ROOT
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from ictbot.runtime import stability_grades, verdicts
    from scripts import contest_readiness as cr

    fmin = settings.strategy_select_forward_min_days if forward_min_days is None else forward_min_days
    vmap = verdicts.load() if vmap is None else vmap
    gmap = stability_grades.load() if gmap is None else gmap
    state = _load_state() if state is None else state
    margin = float(settings.strategy_select_margin)
    sustain = int(settings.strategy_select_sustain)

    results = cr.run_readiness(save=False, forward_min_days=fmin, vmap=vmap, gmap=gmap)
    incumbent = _live_arm()

    # Per-arm risk-adjusted scores (DQ-safe candidates only; never a survival-fail / UNSTABLE arm).
    scores = []
    for r in results:
        sv = r.get("survival") or {}
        grade = (r.get("stability") or {}).get("grade")
        dq_safe = bool(sv.get("passed")) and grade != "UNSTABLE"
        scores.append({
            "arm": r["arm"], "score": _score(r, vmap), "dq_safe": dq_safe,
            "forward_eligible": bool((r.get("forward") or {}).get("forward_eligible")),
            "stability": grade, "verdict": r["verdict"],
        })
    scores.sort(key=lambda s: (s["score"] is not None, s["score"] or 0.0), reverse=True)

    champ = cr.recommend_arm(results)  # strictly-gated best challenger, or STAY
    champion = champ.get("arm")

    # Hysteresis bookkeeping: how many consecutive evals has THIS champion held the slot?
    consecutive = (state.get("consecutive", 0) + 1) if (champion and champion == state.get("last_champion")) else (1 if champion else 0)

    def _s(arm):
        for s in scores:
            if s["arm"] == arm:
                return s["score"]
        return None

    inc_score, champ_score = _s(incumbent), _s(champion) if champion else None
    beats = (champion is not None and champ_score is not None
             and (inc_score is None or champ_score >= inc_score + margin))
    sustained = consecutive >= sustain
    switch = bool(champion and champion != incumbent and beats and sustained)

    if switch:
        reason = (f"{champion} beats {incumbent} by ≥{margin} (score {champ_score:.4f} vs "
                  f"{inc_score if inc_score is None else round(inc_score,4)}) for {consecutive}/{sustain} evals")
    elif champion and champion != incumbent:
        why = []
        if not beats:
            why.append(f"margin not met (champ {champ_score} vs inc {inc_score} + {margin})")
        if not sustained:
            why.append(f"sustain {consecutive}/{sustain}")
        reason = f"hold {incumbent} — challenger {champion} not yet promotable: {', '.join(why)}"
    else:
        reason = champ.get("line") or f"hold {incumbent} — no qualifying challenger"

    return {
        "ts": _now(),
        "current": incumbent,
        "champion": champion,
        "recommended": champion if switch else incumbent,
        "action": "SWITCH" if switch else "STAY",
        "switch": switch,
        "reason": reason,
        "scores": scores,
        "hysteresis": {
            "consecutive": consecutive, "sustain": sustain, "margin": margin,
            "incumbent_score": inc_score, "champion_score": champ_score,
        },
    }


def _persist_state(decision: dict) -> None:
    hist = (_load_state().get("history") or [])[-19:]
    hist.append({"ts": decision["ts"], "champion": decision["champion"], "action": decision["action"]})
    _atomic_write(STATE_FILE, {
        "last_champion": decision["champion"],
        "consecutive": decision["hysteresis"]["consecutive"],
        "history": hist,
    })


def write_recommendation(decision: dict) -> None:
    """Surface the LIVE recommendation (read-only) for the dashboard + audit trail."""
    _atomic_write(RECO_FILE, {
        "recommended_live_arm": decision["recommended"],
        "current_live_arm": decision["current"],
        "action": decision["action"],
        "switch": decision["switch"],
        "reason": decision["reason"],
        "scores": decision["scores"],
        "hysteresis": decision["hysteresis"],
        "apply_cmd": (f"scripts/live_tick.sh {decision['recommended']}" if decision["switch"] else None),
        "auto_apply_live": bool(settings.strategy_auto_apply_live),
        "ts": decision["ts"],
    })


def apply_sim(decision: dict) -> str | None:
    """Drive the SIM track to the recommended arm on a switch (proves the engine on the paper book).
    Gated by STRATEGY_AUTO_SELECT_SIM (default True). Returns the arm saved, or None."""
    if not settings.strategy_auto_select_sim or not decision["switch"]:
        return None
    from ictbot.runtime import strategy_select

    return strategy_select.save(decision["recommended"])


def apply_live(decision: dict) -> str | None:
    """OPT-IN ONLY (STRATEGY_AUTO_APPLY_LIVE, default False): rewrite the live STRATEGY_NAME on a switch.
    Default-off recommend-only path keeps the contest-safety boundary intact. Returns the arm, or None."""
    if not settings.strategy_auto_apply_live or not decision["switch"]:
        return None
    from ictbot.runtime.kill_switch import rewrite_env_key

    rewrite_env_key("STRATEGY_NAME", decision["recommended"])
    return decision["recommended"]


def run(*, dry_run: bool = False) -> dict:
    """Full pass: evaluate → (persist state + recommendation + sim-drive + opt-in live) unless dry-run."""
    decision = evaluate()
    if not dry_run:
        _persist_state(decision)
        write_recommendation(decision)
        decision["sim_applied"] = apply_sim(decision)
        decision["live_applied"] = apply_live(decision)
    return decision
