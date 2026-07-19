#!/usr/bin/env python3
"""
Parameter-sensitivity sweep — find a more ROBUST config per tunable arm.

The stability harness graded the registered arms at their DEFAULT params (`breakout` UNSTABLE,
the locked default only FRAGILE). Each arm ships ONE parameter set, and `breakout`'s own docstring
says "sweep (p_entry, p_exit, rebal_bars) on SIM, never hand-fit." This does exactly that: for each
tunable arm it builds a grid of configs, re-grades every one through the SAME stability probes
(scripts/strategy_stability.probe_strategy), and reports the most-robust config next to the arm's
current default.

OVERFIT HONESTY: picking the best-on-this-window config IS curve-fitting. The defense is to rank by
CROSS-SEGMENT STABILITY (the disjoint-window grade + dd_spread), not best-DD, and to show the 60/40
walk-forward overfit delta per config. A winning config still proves nothing about forward edge — it
must clear the forward check + operator sign-off before it could ever be promoted (decision-record §1:
no long-only edge on this universe).

READ-ONLY / SIM-only: runs backtests + writes ONE report (data/reports/strategy_sweep.md). It NEVER
edits a registered arm, never re-registers the locked default, never persists a verdict, never touches
the journal/selector. It RECOMMENDS; re-registering an arm's defaults is a separate, deliberate edit.

Usage:
  python scripts/strategy_sweep.py                       # sweep all tunable arms
  python scripts/strategy_sweep.py --arm breakout        # one arm
  python scripts/strategy_sweep.py --no-save             # print only
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import scripts.strategy_stability as ss
import scripts.validate_strategy as vs
from ictbot.settings import DATA_DIR
from ictbot.strategy.adapters.breakout import BreakoutStrategy
from ictbot.strategy.adapters.grid import GridStrategy
from ictbot.strategy.adapters.mean_reversion import MeanReversionStrategy
from ictbot.strategy.adapters.momentum import AdaptiveMomentumStrategy
from ictbot.strategy.momentum_allocator import AllocatorParams, cmc_seed_vol_floor

REPORT_PATH = DATA_DIR / "reports" / "strategy_sweep.md"
_GRADE_RANK = {"ROBUST": 0, "FRAGILE": 1, "UNSTABLE": 2}
OVERFIT_SMELL = 0.05   # |hold − fit| worst-week DD beyond this flags a curve-fit smell


def _breakout_configs() -> list[dict]:
    base = AllocatorParams()
    out = []
    for entry, exit_, rb in product((15, 20, 25, 30), (5, 10, 15), (3, 6)):
        if exit_ >= entry:                       # the exit channel must be SHORTER than the entry
            continue
        out.append({"label": f"entry{entry}/exit{exit_}/rb{rb}",
                    "strat": BreakoutStrategy(entry_lb=entry, exit_lb=exit_),
                    "p": replace(base, rebal_bars=rb),
                    "is_default": entry == 20 and exit_ == 5 and rb == 3})
    return out


def _mean_reversion_configs() -> list[dict]:
    base = AllocatorParams()
    out = []
    for window, thr, rb in product((15, 20, 30), (0.5, 1.0, 1.5, 2.0), (3, 6)):
        out.append({"label": f"win{window}/z{thr}/rb{rb}",
                    "strat": MeanReversionStrategy(window=window, threshold=thr),
                    "p": replace(base, rebal_bars=rb),
                    "is_default": window == 20 and thr == 1.0 and rb == 6})
    return out


def _momentum_fast_configs() -> list[dict]:
    # Short-horizon momentum family: FastMomentumStrategy forces lookback=60/rebal=3 internally,
    # so sweep the BASE adaptive arm with custom params (the registered momentum_fast = L60/rb3).
    base = AllocatorParams()
    out = []
    for lb, rb in product((30, 60, 90), (1, 3, 6)):
        out.append({"label": f"L{lb}/rb{rb}",
                    "strat": AdaptiveMomentumStrategy(),
                    "p": replace(base, lookback=lb, rebal_bars=rb),
                    "is_default": lb == 60 and rb == 3})
    return out


def _grid_configs() -> list[dict]:
    base = AllocatorParams()
    out = []
    for window, rb in product((20, 50, 100), (3, 6)):
        out.append({"label": f"win{window}/rb{rb}",
                    "strat": GridStrategy(window=window),
                    "p": replace(base, rebal_bars=rb),
                    "is_default": window == 50 and rb == 6})
    return out


GRIDS = {
    "breakout": _breakout_configs,
    "mean_reversion": _mean_reversion_configs,
    "momentum_fast": _momentum_fast_configs,
    "grid": _grid_configs,
}


def _sweep_key(r: dict):
    """Stability-first: grade, then tightest spread, then lowest worst-case DD."""
    return (_GRADE_RANK.get(r["grade"], 3),
            r["dd_spread"] if r["dd_spread"] is not None else 1.0,
            r["dd_max"] if r["dd_max"] is not None else 1.0, r["label"])


def sweep_arm(close, arm: str, *, vol_floor: float = 0.0) -> list[dict]:
    rows = []
    for cfg in GRIDS[arm]():
        p = replace(cfg["p"], vol_floor=vol_floor) if vol_floor > 0 else cfg["p"]
        s = ss.probe_strategy(close, cfg["strat"], p)
        rows.append({"label": cfg["label"], "is_default": cfg["is_default"], "grade": s["grade"],
                     "pass_rate": s["pass_rate"], "dd_max": s["dd_max"], "dd_spread": s["dd_spread"],
                     "overfit_delta": s["overfit_delta"], "trades_per_week": s["trades_per_week"]})
    return sorted(rows, key=_sweep_key)


def run_sweep(close, *, arms: list[str] | None = None, save: bool = True,
              report_path: Path = REPORT_PATH, now_iso: str | None = None,
              vol_floor: float = 0.0) -> dict:
    now_iso = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    arms = arms or list(GRIDS)
    results = {arm: sweep_arm(close, arm, vol_floor=vol_floor) for arm in arms}
    if save:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_sweep_report(close, results, now_iso=now_iso), encoding="utf-8")
    return results


def _row(r: dict) -> str:
    star = " ⭐" if r["is_default"] else ""
    return (f"| `{r['label']}`{star} | {r['grade']} | {r['pass_rate'] * 100:.0f}% | "
            f"{ss._pct(r['dd_max'])} | {ss._pct(r['dd_spread'])} | "
            f"{ss._pct(r['overfit_delta'], sign=True)} | {r['trades_per_week']:.1f} |")


def _verdict(ranked: list[dict]) -> str:
    best = ranked[0]
    default = next((r for r in ranked if r["is_default"]), None)
    if default is None:
        return f"best `{best['label']}` → {best['grade']}."
    if best["is_default"]:
        return f"the **default** `{default['label']}` is already the most robust ({default['grade']})."
    db = _GRADE_RANK.get(default["grade"], 3) - _GRADE_RANK.get(best["grade"], 3)
    better = "a better GRADE" if db > 0 else f"a tighter spread ({ss._pct(best['dd_spread'])} vs {ss._pct(default['dd_spread'])})"
    smell = " ⚠️ but its overfitΔ is large — treat as a curve-fit smell" if (
        best["overfit_delta"] is not None and abs(best["overfit_delta"]) > OVERFIT_SMELL) else ""
    return (f"`{best['label']}` ({best['grade']}) beats the default `{default['label']}` "
            f"({default['grade']}) — {better}{smell}. Forward-validate before any promotion.")


def render_sweep_report(close, results: dict, *, now_iso: str) -> str:
    out = [
        "# Strategy parameter-sensitivity sweep",
        "",
        f"_Generated by `make sweep_arms` at **{now_iso}** over **{close.shape[0]}** 4h bars × "
        f"**{close.shape[1]}** tokens. Regenerated wholesale each run. ⭐ = the arm's current default._",
        "",
        "Each config is re-graded through the stability harness (disjoint data-window segments + "
        "friction + per-regime + 60/40 holdout). Ranked **stability-first** (grade → tightest spread → "
        "lowest worst-case DD) — NOT best-DD, which would curve-fit. `overfitΔ` = holdout − train "
        f"worst-week DD; |Δ| > {OVERFIT_SMELL * 100:.0f}pts is a curve-fit smell. **A winning config is "
        "a candidate only — it must still clear the forward check + operator sign-off** (no long-only "
        "edge on this universe; decision-record §1).",
        "",
    ]
    for arm, ranked in results.items():
        out += [f"## `{arm}`", "", f"**Verdict:** {_verdict(ranked)}", "",
                "| Config | Grade | segPass | ddMax | spread | overfitΔ | t/wk |",
                "|---|:--:|--:|--:|--:|--:|--:|"]
        out += [_row(r) for r in ranked]
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep each tunable arm's params for a more robust config.")
    ap.add_argument("--arm", choices=list(GRIDS), default=None, help="one arm (default: all tunable)")
    ap.add_argument("--limit", type=int, default=2500)
    ap.add_argument("--no-save", action="store_true", help="print only; don't write the report")
    args = ap.parse_args()

    arms = [args.arm] if args.arm else list(GRIDS)
    print(f"parameter sweep — {', '.join(arms)} (stability-graded, 0.70% binding)"
          f"{'  [no-save]' if args.no_save else ''}")
    # CEX-free: graded on CMC's own 4h candles (the WS stream + seed — the feed the live arm trades
    # on); the swept window grids are 4h-bar counts (native), and the flat cold-start seed is sized
    # correctly via the shared seed vol-floor injected into each config's params.
    mat = vs.load_matrix(args.limit, candle_source="cmc_4h")
    print(f"loaded {mat.shape[0]} CMC-4h bars × {mat.shape[1]} tokens (CEX-free)\n")
    vf = cmc_seed_vol_floor(mat)
    results = run_sweep(mat.to_numpy(dtype=float), arms=arms, save=not args.no_save, vol_floor=vf)

    for arm, ranked in results.items():
        print(f"=== {arm} ({len(ranked)} configs) ===")
        print(f"{'config':24} {'grade':9} {'segPass':>7} {'ddMax':>7} {'spread':>7} {'ovfitΔ':>7} {'t/wk':>6}")
        print("-" * 78)
        for r in ranked[:6]:
            star = "*" if r["is_default"] else " "
            print(f"{star}{r['label']:23} {r['grade']:9} {r['pass_rate'] * 100:6.0f}% "
                  f"{ss._pct(r['dd_max']):>7} {ss._pct(r['dd_spread']):>7} "
                  f"{ss._pct(r['overfit_delta'], sign=True):>7} {r['trades_per_week']:6.1f}")
        if not any(r["is_default"] for r in ranked[:6]):
            d = next(r for r in ranked if r["is_default"])
            print(f"  …default: *{d['label']:22} {d['grade']:9} {d['pass_rate'] * 100:6.0f}% "
                  f"{ss._pct(d['dd_max']):>7} {ss._pct(d['dd_spread']):>7}")
        print(f"  → {_verdict(ranked)}\n")
    if not args.no_save:
        print(f"wrote: {REPORT_PATH}")
    print("READ-ONLY recommender — no arm changed, no verdict persisted, live strategy untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
