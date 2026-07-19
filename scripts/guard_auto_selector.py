#!/usr/bin/env python3
"""
Auto-selector health GUARD — ONE pass. Prints one heartbeat line; exits 0 (healthy) / 1 (broken).

Each pass:
  1. ENGINE (hard)     — subprocess the isolated demo smoke (`demo_auto_selector.py`); exit 0 ⇒ the engine
                         can still drive a real STAY→SWITCH. Fully isolated (its own ALLOCATOR_DATA_DIR tmp),
                         so this never touches live data.
  2. TICK (hard, opt)  — run the REAL `strategy_evaluator.run()` against the live data dir so the live
                         recommendation's timestamp refreshes (a manual stand-in for the dead 12h cron).
                         Recommend-only (STRATEGY_AUTO_APPLY_LIVE=False) + STAY ⇒ the live arm never changes,
                         no SIM write, no .env touch. Disable with --no-tick (read-only pass).
  3. LIVE FILES (hard) — validate `data/reports/strategy_evaluation.json` + `..._state.json` are coherent
                         (STAY consistency, scores ranked, arms registered, state↔eval agree, ts fresh).
  4. API (soft)        — best-effort GET the deployed snapshot; validate `.auto_selector`. Render
                         cold-start/down is INFO, never a failure. Skip with --no-api.

This process must NOT set ALLOCATOR_DATA_DIR (the tick + file reads target the real data dir). Run via
`scripts/guard_auto_selector.sh` (sets PYTHONPATH); the .sh loop wraps this for the 1-hour cadence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO / "src"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

API_URL = "https://bnb-mission-control-api.onrender.com/api/snapshot"
FRESH_MAX_S = 360.0  # after a tick the recommendation ts must be < ~6 min old


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts_age_s(ts: str) -> float | None:
    try:
        return (_now() - datetime.fromisoformat(ts)).total_seconds()
    except Exception:
        return None


def _run_engine_smoke() -> tuple[bool, str]:
    """HARD: the isolated demo must still prove a real STAY→SWITCH (exit 0)."""
    try:
        r = subprocess.run([sys.executable, str(_REPO / "scripts" / "demo_auto_selector.py")],
                           cwd=str(_REPO), capture_output=True, text=True, timeout=120)
    except Exception as e:  # pragma: no cover - defensive
        return False, f"smoke-spawn-error:{e}"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["(no output)"]
        return False, f"smoke-exit-{r.returncode}:{tail[0][:80]}"
    return True, "PASS"


def _tick_live() -> tuple[bool, str]:
    """HARD (opt): refresh the live recommendation by running the REAL evaluator (recommend-only)."""
    try:
        from ictbot.runtime import strategy_evaluator as se
        d = se.run()  # writes RECO + state against the live data dir; STAY ⇒ no arm/SIM/.env change
        return True, f"{d['action']}({d['recommended']})"
    except Exception as e:
        return False, f"tick-error:{type(e).__name__}:{e}"


def _validate_live(*, expect_fresh: bool) -> tuple[bool, str, dict]:
    """HARD: the live evaluation + state files must be coherent. Returns (ok, detail, summary)."""
    from ictbot.settings import DATA_DIR
    from ictbot.strategy import registry

    reports = DATA_DIR / "reports"
    try:
        ev = json.loads((reports / "strategy_evaluation.json").read_text(encoding="utf-8"))
        st = json.loads((reports / "strategy_evaluator_state.json").read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"read-error:{e}", {}

    arms = set(registry.available())
    action = ev.get("action")
    rec, cur = ev.get("recommended_live_arm"), ev.get("current_live_arm")
    switch = bool(ev.get("switch"))
    h = ev.get("hysteresis") or {}
    consec, sustain = h.get("consecutive"), h.get("sustain")
    margin = h.get("margin")
    summary = {"action": action, "current": cur, "recommended": rec,
               "consec": consec, "sustain": sustain}

    def fail(why: str) -> tuple[bool, str, dict]:
        return False, why, summary

    # --- structural ---
    if action not in ("STAY", "SWITCH"):
        return fail(f"bad-action:{action}")
    if switch != (action == "SWITCH"):
        return fail("switch≠action")
    if rec not in arms:
        return fail(f"recommended-unregistered:{rec}")
    if cur not in arms:
        return fail(f"current-unregistered:{cur}")
    if not isinstance(consec, int) or consec < 0:
        return fail(f"bad-consecutive:{consec}")
    if not isinstance(sustain, int) or sustain < 1:
        return fail(f"bad-sustain:{sustain}")
    if margin is None or margin < 0:
        return fail(f"bad-margin:{margin}")

    # --- scores ranked desc (non-None first, then Nones) + all arms registered ---
    vals = [s.get("score") for s in ev.get("scores", [])]
    if any((s.get("arm") not in arms) for s in ev.get("scores", [])):
        bad = [s.get("arm") for s in ev.get("scores", []) if s.get("arm") not in arms]
        return fail(f"scores-unregistered:{bad[:2]}")
    non_none = [v for v in vals if v is not None]
    if non_none != sorted(non_none, reverse=True):
        return fail("scores-not-desc")
    first_none = next((i for i, v in enumerate(vals) if v is None), len(vals))
    if not all(v is None for v in vals[first_none:]):
        return fail("scores-None-before-value")

    # --- STAY vs SWITCH semantics ---
    if action == "STAY":
        if rec != cur:
            return fail("STAY-but-rec≠cur")
        if ev.get("apply_cmd") is not None:
            return fail("STAY-but-apply_cmd-set")
    else:  # SWITCH
        if rec == cur:
            return fail("SWITCH-but-rec==cur")
        if consec < sustain:
            return fail(f"SWITCH-but-consec<{sustain}")
        ci, cc = h.get("incumbent_score"), h.get("champion_score")
        if ci is not None and cc is not None and cc < ci + margin:
            return fail("SWITCH-but-margin-not-met")
        if not str(ev.get("apply_cmd") or "").endswith(rec):
            return fail("SWITCH-apply_cmd-mismatch")

    # --- state ↔ eval consistency (written together by run()) ---
    if st.get("consecutive") != consec:
        return fail("state.consec≠eval.consec")
    hist = st.get("history") or []
    if not hist:
        return fail("empty-history")
    if hist[-1].get("ts") != ev.get("ts") or hist[-1].get("action") != action:
        return fail("state.history[-1]≠eval")
    lc = st.get("last_champion")
    if lc is not None and lc not in arms:
        return fail(f"last_champion-unregistered:{lc}")

    # --- timestamp parse + freshness (only meaningful right after a tick) ---
    age = _ts_age_s(ev.get("ts") or "")
    if age is None:
        return fail("ts-unparseable")
    summary["age_s"] = round(age)
    if expect_fresh and age > FRESH_MAX_S:
        return fail(f"stale-after-tick:{round(age)}s")
    return True, "ok", summary


def _check_api() -> tuple[str, str]:
    """SOFT: best-effort deployed snapshot check. Never fails the guard — returns (tag, detail)."""
    try:
        from ictbot.strategy import registry
        req = urllib.request.Request(API_URL, headers={"User-Agent": "auto-selector-guard"})
        with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310 (fixed https URL)
            snap = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return "INFO", f"unreachable:{type(e).__name__}"
    auto = snap.get("auto_selector")
    if auto is None:
        return "INFO", "auto_selector-null"
    arms = set(registry.available())
    rec, cur, act = auto.get("recommended"), auto.get("current"), auto.get("action")
    if act not in ("STAY", "SWITCH"):
        return "WARN", f"bad-action:{act}"
    if (rec and rec not in arms) or (cur and cur not in arms):
        return "WARN", "unregistered-arm"
    return "ok", str(act)


def run_pass(*, do_tick: bool, do_api: bool) -> tuple[bool, str]:
    eng_ok, eng = _run_engine_smoke()

    tick_ok, tick = (True, "skipped")
    if do_tick:
        tick_ok, tick = _tick_live()

    live_ok, live_detail, summ = _validate_live(expect_fresh=do_tick and tick_ok)

    api_tag = api_detail = None
    if do_api:
        api_tag, api_detail = _check_api()

    ok = eng_ok and tick_ok and live_ok
    ts = _now().isoformat(timespec="seconds")
    parts = [f"[{ts}] GUARD {'ok' if ok else 'FAIL'}",
             f"engine={'PASS' if eng_ok else eng}",
             f"tick={tick}"]
    if live_ok:
        parts.append(f"live={summ.get('action')}({summ.get('current')})")
        parts.append(f"consec={summ.get('consec')}/{summ.get('sustain')}")
        if "age_s" in summ:
            parts.append(f"fresh={summ['age_s']}s")
    else:
        parts.append(f"live=ERR({live_detail})")
    if do_api:
        parts.append(f"api={api_tag}({api_detail})")
    return ok, "  ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-selector health guard — one pass.")
    ap.add_argument("--no-tick", action="store_true",
                    help="read-only: don't refresh the live recommendation (skip the evaluator tick)")
    ap.add_argument("--no-api", action="store_true", help="skip the best-effort deployed API check")
    args = ap.parse_args()
    ok, line = run_pass(do_tick=not args.no_tick, do_api=not args.no_api)
    print(line, flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
