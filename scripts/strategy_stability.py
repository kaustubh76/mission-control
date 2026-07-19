#!/usr/bin/env python3
"""
Strategy survival-verdict STABILITY harness.

The campaign reports ONE worst-week-DD per arm from the current data window — but that number
is noisy: `breakout` swung 14.3% → 31.6% as the 2500-bar window moved. A single-window PASS/FAIL
can't tell a trustworthy contest arm from a lucky one. This harness measures how STABLE each arm's
verdict is across four axes and grades it robust / fragile / unstable, so we pick an arm whose PASS
holds up — not its best window.

For each real arm (registry arms minus the BNB_STRATEGY_0X aliases), all four probes reuse the
validated backtest engine (engine.portfolio_replay) on ONE fetched matrix — no new backtest math:

  (a) DATA-WINDOW — split the series into ~5 DISJOINT calendar segments and compute the worst-week
      DD for windows starting in each (rolling_window_stats start_mask). Trailing-length windows all
      share the recent tail and HIDE the swing; disjoint segments expose it. The spread across
      segments is the noise metric.
  (b) FRICTION — re-simulate at 0.30% / 0.70% / 1.0% round-trip; does the verdict flip?
  (c) PER-REGIME — condition the worst-week DD on the entry regime (BULL/BEAR/CHOP) to see which
      regime blows the arm up.
  (d) WALK-FORWARD — a 60/40 train/test split; the DD delta flags overfit/regime fragility.

GRADE (keys off the WORST plausible window + the noise, not the mean):
  ROBUST   = every segment passes, dd_max < 20%, spread < 8%, friction-stable, no BEAR blowup, active
  FRAGILE  = mostly passes (≥75%), dd_max < 25%, active — but a thin margin / one axis wobbles
  UNSTABLE = a segment fails, dd_max ≥ 25%, or a verdict flip on ANY axis

SIM-only / read-only: backtests + ONE report write (data/reports/strategy_stability.md). It NEVER
calls verdicts.record, NEVER touches data/journal/ or strategy_select.json, NEVER ticks. A companion
to the campaign — the campaign stays the single source of truth for the persisted verdicts.

Usage:
  python scripts/strategy_stability.py                 # all arms, write the report
  python scripts/strategy_stability.py --arm breakout  # one arm
  python scripts/strategy_stability.py --no-save       # print only
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import scripts.validate_strategy as vs
from ictbot.engine.acceptance import DEFAULT as GATE
from ictbot.engine.portfolio_replay import (
    ONE_WAY_30BPS,
    ONE_WAY_70BPS,
    evaluate,
    returns_matrix,
    rolling_window_stats,
    simulate,
)
from ictbot.settings import DATA_DIR, settings
from ictbot.strategy import regime_score as _rs
from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import cmc_seed_vol_floor
from scripts.strategy_campaign import alias_for, real_arms

BINDING = ONE_WAY_70BPS
FRICTIONS = {"0.30%RT": ONE_WAY_30BPS, "0.70%RT": ONE_WAY_70BPS, "1.0%RT": 0.0050}
REGIMES = ("BULL", "BEAR", "CHOP")
WARMUP = 160
N_SEGMENTS = 5
MIN_SEG = 420  # >= ~10 weeks of 4h bars per segment (enough rolling windows)
LOW_CONF = 20  # a regime with < this many windows is low-confidence
REPORT_PATH = DATA_DIR / "reports" / "strategy_stability.md"

_GRADE_RANK = {"ROBUST": 0, "FRAGILE": 1, "UNSTABLE": 2}


def load_universe(limit: int):
    """One fetch → aligned CMC 4h close matrix (CEX-free — the WS stream + seed, the feed the live
    arm trades on; reused across every arm + probe)."""
    return vs.load_matrix(limit, candle_source="cmc_4h")


def _caps(close: np.ndarray) -> np.ndarray:
    return _rs.cap_series(close, floor=settings.alloc_cap_floor,
                          ceiling=settings.alloc_cap_ceiling, ma_window=settings.alloc_breadth_ma)


def window_segments(n: int, warmup: int, n_seg: int = N_SEGMENTS) -> list[tuple[int, int]]:
    """Up to `n_seg` DISJOINT calendar segments over [warmup, n) for the variance probe."""
    span = n - warmup
    if span < MIN_SEG:
        return []
    win = max(MIN_SEG, span // n_seg)
    out = []
    for s in range(n_seg):
        a = warmup + s * win
        if a >= n - 42:                          # need at least one full 7-day window ahead
            break
        out.append((a, min(a + win, n)))
    return out


def sweep_windows(eq: np.ndarray, warmup: int, segments: list[tuple[int, int]]) -> list[dict]:
    rows = []
    for a, b in segments:
        mask = np.zeros(len(eq), dtype=bool)
        mask[a:b] = True
        s = rolling_window_stats(eq, warmup, start_mask=mask)
        dd = s.get("worst_week_dd")
        rows.append({"a": a, "b": b, "n": s.get("n_windows", 0), "dd": dd,
                     "pass": dd is not None and dd < GATE.max_worst_week_dd})
    return rows


def sweep_frictions(close: np.ndarray, wp: np.ndarray, warmup: int, tpw: float) -> dict:
    rets = returns_matrix(close)
    out = {}
    for label, ow in FRICTIONS.items():
        eq, _ = simulate(wp, rets, ow)
        dd = rolling_window_stats(eq, warmup).get("worst_week_dd")
        out[label] = {"dd": dd, "pass": (dd is not None and dd < GATE.max_worst_week_dd
                                         and tpw >= GATE.min_trades_per_week)}
    return out


def regime_breakdown_dd(close: np.ndarray, eq: np.ndarray, warmup: int) -> dict:
    labels = _rs.regime_labels(close)
    out = {}
    for reg in REGIMES:
        s = rolling_window_stats(eq, warmup, start_mask=(labels == reg))
        n = s.get("n_windows", 0)
        out[reg] = {"dd": s.get("worst_week_dd") if n else None, "n": n, "low_conf": n < LOW_CONF}
    return out


def walk_forward_dd(close: np.ndarray, strat, p, warmup: int) -> dict:
    """60/40 train/test DD delta — recompute weights on each half (genuine out-of-sample)."""
    split = int(close.shape[0] * 0.6)

    def _half(sub: np.ndarray):
        if sub.shape[0] < warmup + 42:
            return None
        st = evaluate(sub, strat.weight_path(sub, p=p, cap_series=_caps(sub)),
                      one_way=BINDING, warmup=warmup)
        return st.get("worst_week_dd")

    fit, hold = _half(close[:split]), _half(close[split:])
    delta = (hold - fit) if (fit is not None and hold is not None) else None
    return {"fit_dd": fit, "hold_dd": hold, "overfit_delta": delta}


def score_arm(windows: list[dict], frictions: dict, regimes: dict, wf: dict, tpw: float) -> dict:
    seg = [w for w in windows if w["dd"] is not None]
    dds = [w["dd"] for w in seg]
    pass_rate = (sum(w["pass"] for w in seg) / len(seg)) if seg else 0.0
    dd_min = min(dds) if dds else None
    dd_max = max(dds) if dds else None
    dd_spread = (dd_max - dd_min) if dds else None
    flips = sum(1 for i in range(1, len(seg)) if seg[i]["pass"] != seg[i - 1]["pass"])
    friction_stable = len({f["pass"] for f in frictions.values()}) <= 1
    bear = regimes.get("BEAR", {})
    bear_blowup = bear.get("dd") is not None and bear["dd"] >= GATE.max_worst_week_dd
    rds = {r: v["dd"] for r, v in regimes.items() if v["dd"] is not None}
    worst_regime = max(rds, key=rds.get) if rds else None
    active = tpw >= GATE.min_trades_per_week

    if (pass_rate == 1.0 and dd_max is not None and dd_max < 0.20 and dd_spread is not None
            and dd_spread < 0.08 and friction_stable and not bear_blowup and active):
        grade = "ROBUST"
    elif pass_rate >= 0.75 and dd_max is not None and dd_max < GATE.max_worst_week_dd and active:
        grade = "FRAGILE"
    else:
        grade = "UNSTABLE"

    return {"grade": grade, "pass_rate": pass_rate, "dd_min": dd_min, "dd_max": dd_max,
            "dd_spread": dd_spread, "flips": flips, "friction_stable": friction_stable,
            "bear_blowup": bear_blowup, "worst_regime": worst_regime, "trades_per_week": tpw,
            "overfit_delta": wf.get("overfit_delta"), "n_segments": len(seg)}


def probe_strategy(close: np.ndarray, strat, p) -> dict:
    """Run all four stability probes for a strategy INSTANCE + params and score it. The
    instance-based core shared by `_evaluate_arm` (registered arms) and the parameter sweep
    (scripts/strategy_sweep.py constructs ad-hoc instances)."""
    warmup = max(WARMUP, strat.warmup(p))
    caps = _caps(close)
    wp = strat.weight_path(close, p=p, cap_series=caps)
    rets = returns_matrix(close)
    eq, txns = simulate(wp, rets, BINDING)
    n_eff = len(rets) - 1
    tpw = txns * 42 / n_eff if n_eff else 0.0
    segments = window_segments(close.shape[0], warmup)
    windows = sweep_windows(eq, warmup, segments)
    frictions = sweep_frictions(close, wp, warmup, tpw)
    regimes = regime_breakdown_dd(close, eq, warmup)
    wf = walk_forward_dd(close, strat, p, warmup)
    score = score_arm(windows, frictions, regimes, wf, tpw)
    return {**score, "windows": windows, "frictions": frictions, "regimes": regimes, "wf": wf}


def _evaluate_arm(close: np.ndarray, arm: str, *, vol_floor: float = 0.0) -> dict:
    # CMC 4h grading (native 4h params — the candles ARE the 4h grid). The flat cold-start seed is
    # sized correctly by injecting the shared seed vol-floor (computed once from the time-indexed
    # matrix in main(); same scalar the live tick / survival_for use).
    strat = registry.get(arm)
    p = strat.default_params()
    if vol_floor > 0:
        from dataclasses import replace

        p = replace(p, vol_floor=vol_floor)
    return {"arm": arm, "alias": alias_for(arm), **probe_strategy(close, strat, p)}


def _stability_key(r: dict):
    """Stability-first: by grade, then smallest DD spread (tightest = most trustworthy)."""
    return (_GRADE_RANK.get(r["grade"], 3), r["dd_spread"] if r["dd_spread"] is not None else 1.0,
            r["arm"])


def run_stability(close: np.ndarray, *, arms: list[str] | None = None, save: bool = True,
                  report_path: Path = REPORT_PATH, now_iso: str | None = None,
                  grades_path: Path | None = None, vol_floor: float = 0.0) -> list[dict]:
    now_iso = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    arms = arms or real_arms()
    results = [_evaluate_arm(close, a, vol_floor=vol_floor) for a in arms]
    if save:
        from ictbot.runtime import stability_grades
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_stability_report(close, results, now_iso=now_iso),
                               encoding="utf-8")
        # JSON sidecar for the dashboard badge (merge: a partial --arm run won't wipe the rest).
        stability_grades.record({r["arm"]: {"grade": r["grade"], "ts": now_iso} for r in results},
                                path=grades_path)
    return results


def _pct(x, sign=False) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.1f}%" if sign else f"{x * 100:.1f}%"


def _regime_cell(regimes: dict, reg: str) -> str:
    v = regimes.get(reg, {})
    tag = " ⚠️lc" if v.get("low_conf") and v.get("dd") is not None else ""
    return _pct(v.get("dd")) + tag


def render_stability_report(close: np.ndarray, results: list[dict], *, now_iso: str) -> str:
    ranked = sorted(results, key=_stability_key)
    n = close.shape[0]
    out = [
        "# Strategy survival-verdict STABILITY report",
        "",
        f"_Generated by `make stability` at **{now_iso}** over **{n}** 4h bars × "
        f"**{close.shape[1]}** tokens. Regenerated wholesale each run. Companion to "
        "[strategy_campaign.md](../../docs/strategy_campaign.md) — does NOT persist verdicts._",
        "",
        "**Why.** A single-window worst-week DD is noisy (`breakout` swung 14.3% → 31.6%). This "
        "grades each arm by how STABLE its survival verdict is across disjoint calendar segments, "
        "friction levels, regimes, and a 60/40 holdout. Ranked stability-first (grade, then spread). "
        "The 25% worst-week-DD rail is the gate; `dd_max`/`spread` are the trust signals.",
        "",
        "| Arm | Alias | Grade | seg pass | ddMin | ddMax | spread | flips | worstRegime | overfitΔ | t/wk |",
        "|---|---|:--:|--:|--:|--:|--:|--:|:--:|--:|--:|",
    ]
    for r in ranked:
        out.append(
            f"| `{r['arm']}` | {r.get('alias') or '—'} | {r['grade']} | "
            f"{r['pass_rate'] * 100:.0f}% ({r['n_segments']}) | {_pct(r['dd_min'])} | "
            f"{_pct(r['dd_max'])} | {_pct(r['dd_spread'])} | {r['flips']} | "
            f"{r['worst_regime'] or '—'} | {_pct(r['overfit_delta'], sign=True)} | "
            f"{r['trades_per_week']:.1f} |"
        )
    out += ["", "## Per-regime worst-week DD (entry-conditioned)", "",
            "| Arm | BULL | BEAR | CHOP |", "|---|--:|--:|--:|"]
    for r in ranked:
        cells = " | ".join(_regime_cell(r["regimes"], reg) for reg in REGIMES)
        out.append(f"| `{r['arm']}` | {cells} |")
    out += [
        "",
        "_Grades — **ROBUST**: every segment passes, ddMax<20%, spread<8%, friction-stable, no BEAR "
        "blowup, ≥7 t/wk. **FRAGILE**: mostly passes (≥75%), ddMax<25%. **UNSTABLE**: a segment fails, "
        "ddMax≥25%, or a verdict flip on any axis. `⚠️lc` = low-confidence regime (<20 windows). No "
        "arm is contest-eligible on stability alone — pair with the forward check + operator sign-off._",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade each arm's survival-verdict stability.")
    ap.add_argument("--arm", default=None, help="one arm (default: all real arms)")
    ap.add_argument("--limit", type=int, default=2500)
    ap.add_argument("--no-save", action="store_true", help="print only; don't write the report")
    args = ap.parse_args()

    arms = [args.arm] if args.arm else real_arms()
    print(f"stability harness — {len(arms)} arm(s), {N_SEGMENTS} disjoint segments × "
          f"{len(FRICTIONS)} frictions × {len(REGIMES)} regimes + 60/40 holdout, 0.70% binding"
          f"{'  [no-save]' if args.no_save else ''}")
    close = load_universe(args.limit)
    print(f"loaded {close.shape[0]} CMC-4h bars × {close.shape[1]} tokens ({list(close.columns)})\n")
    vf = cmc_seed_vol_floor(close)  # cold-start seed protection (from the time-indexed matrix)
    results = run_stability(close.to_numpy(dtype=float), arms=arms, save=not args.no_save, vol_floor=vf)

    ranked = sorted(results, key=_stability_key)
    print(f"{'arm':22} {'grade':9} {'segPass':>8} {'ddMax':>7} {'spread':>7} "
          f"{'flips':>5} {'worstReg':>9} {'ovfitΔ':>7} {'t/wk':>6}")
    print("-" * 92)
    for r in ranked:
        print(f"{r['arm']:22} {r['grade']:9} {r['pass_rate'] * 100:6.0f}% "
              f"{_pct(r['dd_max']):>7} {_pct(r['dd_spread']):>7} {r['flips']:>5} "
              f"{(r['worst_regime'] or '—'):>9} {_pct(r['overfit_delta'], sign=True):>7} "
              f"{r['trades_per_week']:6.1f}")
    if not args.no_save:
        print(f"\nwrote: {REPORT_PATH}")
    print("\nSIM-only / read-only — no verdicts persisted, live strategy untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
