#!/usr/bin/env python3
"""
A/B race — ONE cycle. Tracks the two contest arms head-to-head on the SAME live CMC data and auto-locks the
live arm to the leader when the signal sustains.

Each cycle:
  1. LIVE tick   — `run_allocator --mode live` on the current STRATEGY_NAME (real; TWAK auto-executes on the
                   CMC signal). With --paper this becomes `--quote-only` (zero spend, for the smoke).
  2. SHADOW tick — `run_allocator --mode sim --strategy <other-arm>` in an ISOLATED ALLOCATOR_DATA_DIR
                   (data/ab/<arm>) → a paper track on the same live CMC, zero spend, separate journal.
  3. COMPARE     — risk-adjusted score per arm over the race window: (nav/anchor − 1) − worst_drawdown,
                   tracked incrementally in data/ab/race_state.json (anchor + peak + worst per arm).
  4. AUTO-LOCK   — if the SHADOW beats the LIVE arm by ≥ margin for ≥ sustain consecutive cycles, flip the
                   live arm: rewrite_env_key("STRATEGY_NAME", winner) → the next live tick runs it; swap roles
                   and re-anchor both. Anti-chasing via margin+sustain; candidates restricted to the two
                   pre-vetted DQ-safe ROBUST arms.

Keys stay in the LOCAL .env. The .sh wrapper loops this on a cadence. Exits 0 (cycle ran) / non-zero (error).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO / "src"), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CANDIDATES = ("mean_reversion", "momentum_cmc")          # the only two arms the race may lock (both DQ-safe/ROBUST)
AB_DIR = _REPO / "data" / "ab"
RACE_STATE = AB_DIR / "race_state.json"
LIVE_JOURNAL = _REPO / "data" / "journal" / "allocator_live.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _last_nav(journal: Path) -> float | None:
    """Latest REBALANCE nav_after in a journal, or None."""
    try:
        navs = [json.loads(line).get("nav_after")
                for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()
                and json.loads(line).get("event") == "REBALANCE"]
        navs = [float(n) for n in navs if isinstance(n, (int, float)) and n > 0]
        return navs[-1] if navs else None
    except Exception:
        return None


def _shadow_journal(arm: str) -> Path:
    return AB_DIR / arm / "journal" / "allocator_journal.jsonl"


def _load_state() -> dict:
    try:
        return json.loads(RACE_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    RACE_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RACE_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, RACE_STATE)


def _arm_track(st: dict, arm: str, nav: float) -> dict:
    """Incrementally update {anchor, peak, worst} for an arm given the latest nav; returns the entry."""
    t = dict(st.get("arms", {}).get(arm) or {})
    if not t.get("anchor"):
        t = {"anchor": nav, "peak": nav, "worst": 0.0}
    t["peak"] = max(float(t["peak"]), nav)
    if t["peak"] > 0:
        t["worst"] = max(float(t["worst"]), (t["peak"] - nav) / t["peak"])
    t["last"] = nav
    return t


def _score(t: dict) -> float | None:
    """Risk-adjusted race score: return-since-anchor − worst-drawdown-since-anchor."""
    a, last = t.get("anchor"), t.get("last")
    if not (a and a > 0 and last):
        return None
    return (last / a - 1.0) - float(t.get("worst", 0.0))


def _run_tick(*, mode: str, strategy: str | None = None, data_dir: Path | None = None,
              quote_only: bool = False, timeout: int = 600) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{_REPO}/src"
    env["CMC_ONLY"] = "true"
    if data_dir is not None:
        env["ALLOCATOR_DATA_DIR"] = str(data_dir)          # isolate the shadow track
    else:
        env.pop("ALLOCATOR_DATA_DIR", None)                # live arm hits the REAL data dir
    py = str(_REPO / ".venv" / "bin" / "python")
    if not Path(py).exists():
        py = "python3"
    cmd = [py, str(_REPO / "scripts" / "run_allocator.py")]
    cmd += ["--quote-only"] if quote_only else ["--mode", mode]
    if strategy:
        cmd += ["--strategy", strategy]
    try:
        return subprocess.run(cmd, cwd=str(_REPO), env=env, capture_output=True,
                              text=True, timeout=timeout).returncode
    except Exception:
        return 99


def run_cycle(*, paper: bool, margin: float, sustain: int, dry_lock: bool) -> dict:
    st = _load_state()
    live = st.get("live_arm") or (os.environ.get("STRATEGY_NAME") or CANDIDATES[0])
    shadow = next((a for a in CANDIDATES if a != live), CANDIDATES[1])

    # 1. LIVE tick (real, unless --paper → quote-only) on the current live arm.
    live_rc = _run_tick(mode="live", quote_only=paper)
    # 2. SHADOW tick — paper, isolated dir, forced to the other arm.
    shadow_rc = _run_tick(mode="sim", strategy=shadow, data_dir=AB_DIR / shadow)

    # 3. Read NAVs + update per-arm tracks.
    live_nav = _last_nav(LIVE_JOURNAL)
    shadow_nav = _last_nav(_shadow_journal(shadow))
    arms = dict(st.get("arms") or {})
    if live_nav:
        arms[live] = _arm_track(st, live, live_nav)
    if shadow_nav:
        arms[shadow] = _arm_track(st, shadow, shadow_nav)
    st["arms"] = arms

    live_sc, shadow_sc = _score(arms.get(live, {})), _score(arms.get(shadow, {}))

    # 4. Compare + anti-chasing sustain counter.
    beats = (live_sc is not None and shadow_sc is not None and shadow_sc >= live_sc + margin)
    consecutive = (st.get("consecutive", 0) + 1) if beats else 0
    locked = None
    if beats and consecutive >= sustain and not dry_lock:
        from ictbot.runtime.kill_switch import rewrite_env_key
        rewrite_env_key("STRATEGY_NAME", shadow)
        locked = shadow
        # swap roles + re-anchor both from here so the race continues fairly post-flip
        live, shadow = shadow, live
        consecutive = 0
        for a in (live, shadow):
            if arms.get(a, {}).get("last"):
                arms[a] = {"anchor": arms[a]["last"], "peak": arms[a]["last"], "worst": 0.0, "last": arms[a]["last"]}
    would_lock = bool(beats and consecutive >= sustain and dry_lock)

    st.update(live_arm=live, shadow_arm=shadow, consecutive=consecutive,
              margin=margin, sustain=sustain, ts=_now())
    hist = (st.get("history") or [])[-49:]
    hist.append({"ts": _now(), "live": live, "shadow": shadow,
                 "live_score": live_sc, "shadow_score": shadow_sc, "locked": locked})
    st["history"] = hist
    _save_state(st)

    return {"ts": _now(), "live_arm": live, "shadow_arm": shadow, "live_rc": live_rc,
            "shadow_rc": shadow_rc, "live_score": live_sc, "shadow_score": shadow_sc,
            "consecutive": consecutive, "sustain": sustain, "margin": margin,
            "locked": locked, "would_lock": would_lock}


def _fmt(x) -> str:
    return "—" if x is None else f"{x:+.4f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B race — one cycle (live arm vs shadow arm on live CMC).")
    ap.add_argument("--paper", action="store_true", help="force the LIVE tick to --quote-only (zero spend, smoke)")
    ap.add_argument("--margin", type=float, default=float(os.environ.get("AB_MARGIN", "0.01")),
                    help="risk-adj edge the shadow must beat the live arm by to count toward a lock")
    ap.add_argument("--sustain", type=int, default=int(os.environ.get("AB_SUSTAIN", "2")),
                    help="consecutive cycles the shadow must lead before the live arm flips")
    ap.add_argument("--dry-lock", action="store_true", help="compute the lock decision but do NOT flip .env")
    args = ap.parse_args()

    d = run_cycle(paper=args.paper, margin=args.margin, sustain=args.sustain, dry_lock=args.dry_lock)
    tag = (f"LOCKED→{d['locked']}" if d["locked"]
           else ("WOULD-LOCK" if d["would_lock"] else f"lead {d['consecutive']}/{d['sustain']}"))
    print(f"[{d['ts']}] RACE live={d['live_arm']}({_fmt(d['live_score'])},rc{d['live_rc']}) "
          f"shadow={d['shadow_arm']}({_fmt(d['shadow_score'])},rc{d['shadow_rc']}) "
          f"margin={d['margin']} · {tag}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
