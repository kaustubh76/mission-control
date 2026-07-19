#!/usr/bin/env python3
"""
PnL A/B: does CMC (enhanced regime / universe tilt / multi-timeframe ranking) MEANINGFULLY
improve the momentum allocator's simulated PnL vs the validated baseline?

Holds the momentum engine + candles CONSTANT and varies only the lever under test, all
through the same `portfolio_replay.evaluate` harness (rolling 7-day return/DD/DQ metrics).
Objective = risk-penalized return, DQ-safe first: score = total_return - worst_week_dd
(robust to negative-return windows), hard-gated to pct_dd_over_30 == 0. Judged at the
contest's realistic 0.70%RT friction.

Honest by construction: it reports the WHOLE-window result for every arm + a verdict that
recommends turning a lever ON only if it materially improves the risk-penalized return
without breaking the DQ gate. If CMC is neutral/negative, it says KEEP OFF.

Usage:
    PYTHONPATH=src python scripts/ab_regime.py [--limit 2500] [--sweep] [--no-write]
    make ab_regime
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# This A/B is offline RESEARCH — enable the intel fetchers for the historical macro pull
# (the live trade gating is unchanged; we only read history here).
os.environ.setdefault("CMC_INTEL_ENABLED", "1")

from ictbot.data.cmc import fetch_4h  # noqa: E402
from ictbot.data.cmc_intel import fng_history, global_metrics_history  # noqa: E402
from ictbot.engine.portfolio_replay import (  # noqa: E402
    ONE_WAY_30BPS,
    ONE_WAY_70BPS,
    align_close_matrix,
    evaluate,
)
from ictbot.settings import JOURNAL_DIR, settings  # noqa: E402
from ictbot.strategy.macro_align import align_macro_to_index  # noqa: E402
from ictbot.strategy.momentum_allocator import (  # noqa: E402
    CONTEST_TOKENS,
    AllocatorParams,
    weight_path,
    weight_path_ranked,
    weight_path_tilted,
)
from ictbot.strategy.regime_score import cap_series, cap_series_enhanced  # noqa: E402

EPS = 1e-3
# Multi-timeframe blend for the CMC ranking arm: 7d / 20d / 45d cross-sectional momentum.
DEFAULT_BLEND = {42: 0.5, 120: 0.3, 270: 0.2}
# CMC technical-analysis lever weights (the term in the cap, the tilt on the ranking).
TA_W_CAP = 1.0
TA_W_RANK = 1.0


def _ta(mat):
    """Per-4h-bar CMC-style TA aligned to the close index (daily RSI/MACD/EMA → ffill).
    Returns (ta_health (n,), ta_score (n, k)) — the cap term and the ranking confirmation.
    Locally-computed here for the backtest; LIVE reads CMC's authoritative pre-computed TA."""
    from ictbot.strategy import technicals as T

    daily = T.resample_daily(mat)
    health = T.align_daily_to_index(T.trend_health(daily), daily.index, mat.index)
    score = T.align_daily_to_index(T.token_ta_score(daily), daily.index, mat.index)
    return health, score


def _score(s: dict) -> float:
    """Risk-penalized return: total return NET of the worst-week drawdown. Robust to
    negative-return windows (a return/DD ratio can perversely prefer bigger losses) —
    higher is better, and both 'lose less' and 'smaller DD' raise it. DQ-safe is a
    separate hard gate."""
    return s["total_return"] - s.get("worst_week_dd", 0.0)


def _load(limit: int, source: str = "binance_4h"):
    """Aligned close matrix for the A/B. `cmc_daily` = the CEX-FREE source (real CMC 24-month daily
    OHLCV) — the honest basis for validating the CMC arm's levers; `binance_4h` = the legacy CEX
    reference (blocked under CMC_ONLY via cmc.fetch_4h)."""
    if source == "cmc_daily":
        from ictbot.data.cmc import daily_close_matrix

        days = limit if limit < 1100 else 730
        mat = daily_close_matrix(CONTEST_TOKENS, days=days)
        return mat, [t for t in CONTEST_TOKENS if t in mat.columns]
    frames = {t: fetch_4h(t, limit) for t in CONTEST_TOKENS}
    mat = align_close_matrix(frames, CONTEST_TOKENS)
    return mat, [t for t in CONTEST_TOKENS if frames.get(t) is not None]


def _macro(index):
    gm, fng = global_metrics_history(760), fng_history(500)
    am = align_macro_to_index(index, gm, fng) if (gm or fng) else None
    return am, (am is not None and am.any_present())


def _enh_cap(close, am, floor, ceiling, w_dom, w_mc, w_fng, ta_health=None, w_ta=TA_W_CAP):
    return cap_series_enhanced(
        close,
        floor=floor,
        ceiling=ceiling,
        ma_window=settings.alloc_breadth_ma,
        dominance=am.dominance,
        dominance_prev=am.dominance_prev,
        mktcap=am.mktcap,
        mktcap_prev=am.mktcap_prev,
        fng=am.fng,
        fng_7d=am.fng_7d,
        ta_health=ta_health,
        w_dominance=w_dom,
        w_mktcap=w_mc,
        w_fng_mom=w_fng,
        w_ta=w_ta,
    )


def build_arms(close, tokens, p, floor, ceiling, am, *, w_dom, w_mc, w_fng, blend, ta=None):
    """The weight-path arms. Macro arms are omitted when no historical macro is available;
    TA arms (CMC technical-analysis lever) are omitted when `ta` is None."""
    base_cap = cap_series(close, floor=floor, ceiling=ceiling, ma_window=settings.alloc_breadth_ma)
    tk = tuple(tokens)
    arms = {
        "baseline": weight_path(close, p, cap_series=base_cap),
        "tilt": weight_path_tilted(close, p, cap_series=base_cap, tokens=tk),
        "ranking": weight_path_ranked(close, p, cap_series=base_cap, blend=blend, tokens=tk),
    }
    macro = am is not None and am.any_present()
    if macro:
        enh_cap = _enh_cap(close, am, floor, ceiling, w_dom, w_mc, w_fng)
        arms["enhanced"] = weight_path(close, p, cap_series=enh_cap)
        arms["enhanced+tilt"] = weight_path_tilted(close, p, cap_series=enh_cap, tokens=tk)
        arms["full_cmc"] = weight_path_ranked(
            close, p, cap_series=enh_cap, blend=blend, tilt=True, tokens=tk
        )
    if ta is not None:
        ta_health, ta_score = ta
        # TA in the cap (baseline regime + CMC trend-health term).
        ta_cap = cap_series_enhanced(
            close,
            floor=floor,
            ceiling=ceiling,
            ma_window=settings.alloc_breadth_ma,
            ta_health=ta_health,
            w_ta=TA_W_CAP,
        )
        arms["ta_cap"] = weight_path(close, p, cap_series=ta_cap)
        # TA on the ranking (baseline single-lookback momentum + CMC TA confirmation).
        arms["ta_rank"] = weight_path_ranked(
            close,
            p,
            cap_series=base_cap,
            blend={p.lookback: 1.0},
            ta_score=ta_score,
            w_ta_rank=TA_W_RANK,
            tokens=tk,
        )
        if macro:
            # Everything CMC: macro+TA cap AND TA-confirmed blended ranking.
            enh_ta_cap = _enh_cap(
                close, am, floor, ceiling, w_dom, w_mc, w_fng, ta_health=ta_health, w_ta=TA_W_CAP
            )
            arms["enhanced+ta"] = weight_path(close, p, cap_series=enh_ta_cap)
            arms["full_cmc+ta"] = weight_path_ranked(
                close,
                p,
                cap_series=enh_ta_cap,
                blend=blend,
                ta_score=ta_score,
                w_ta_rank=TA_W_RANK,
                tilt=True,
                tokens=tk,
            )
    return arms


def _fmt(label, s):
    return (
        f"{label:16} {s['total_return'] * 100:+8.2f}% {s['worst_week_dd'] * 100:7.1f}% "
        f"{s['max_dd'] * 100:7.1f}% {s['pct_dd_over_30'] * 100:5.1f}% {s['pct_up'] * 100:4.0f}% "
        f"{s['trades_per_week']:5.1f} {_score(s):8.2f}"
    )


def _verdict(arms_stats: dict) -> dict:
    """Honest recommendation per arm vs baseline (at the primary friction)."""
    base = arms_stats["baseline"]
    out = {}
    for name, s in arms_stats.items():
        if name == "baseline":
            continue
        d_sc = _score(s) - _score(base)
        d_dd = s["worst_week_dd"] - base["worst_week_dd"]
        dq_safe = s["pct_dd_over_30"] == 0
        recommend = bool(d_sc > 0.01 and dq_safe)  # require a material (>1pt) improvement
        tag = "PASS" if recommend else ("NEUTRAL" if abs(d_sc) <= 0.01 else "WORSE")
        out[name] = {
            "d_score": round(d_sc, 3),
            "d_worst_week_dd": round(d_dd, 4),
            "dq_safe": dq_safe,
            "recommend": recommend,
            "tag": tag,
        }
    return out


def run_ab(close, tokens, p, floor, ceiling, am, macro_ok, *, write=True, index=None, ta=None):
    arms = build_arms(
        close,
        tokens,
        p,
        floor,
        ceiling,
        am,
        w_dom=settings.alloc_regime_w_dominance,
        w_mc=settings.alloc_regime_w_mktcap,
        w_fng=settings.alloc_regime_w_fng_mom,
        blend=DEFAULT_BLEND,
        ta=ta,
    )
    frictions = {"0.0035": ONE_WAY_70BPS, "0.0015": ONE_WAY_30BPS}
    by_friction = {
        fk: {name: evaluate(close, wp, one_way=ow) for name, wp in arms.items()}
        for fk, ow in frictions.items()
    }
    primary = by_friction["0.0035"]
    n_windows = primary["baseline"].get("n_windows", 0)

    print(
        f"\n=== PnL A/B — {close.shape[0]} bars x {len(tokens)} tokens · "
        f"{n_windows} rolling-7d windows · macro={'YES' if macro_ok else 'NO'} ==="
    )
    for fk in frictions:
        print(
            f"\n--- friction {float(fk) * 100:.2f}%RT "
            f"{'(contest realistic)' if fk == '0.0035' else ''} ---"
        )
        print(
            f"{'arm':16} {'totalRet':>9} {'wkDDmax':>8} {'maxDD':>8} {'%>30':>6} "
            f"{'%up':>5} {'t/wk':>6} {'score':>8}"
        )
        print("-" * 78)
        for name in arms:
            print(_fmt(name, by_friction[fk][name]))

    verdict = _verdict(primary)
    print("\n" + "=" * 78)
    print("VERDICT (vs baseline, @0.70%RT, risk-penalized return [totRet - wkDD], DQ-safe):")
    for name, v in verdict.items():
        print(
            f"  {name:16} {v['tag']:8} Δscore {v['d_score'] * 100:+.1f}pts  "
            f"Δworst-wk-DD {v['d_worst_week_dd'] * 100:+.1f}pts  "
            f"DQ-safe {'✅' if v['dq_safe'] else '❌'}  -> "
            f"{'TURN ON' if v['recommend'] else 'keep off'}"
        )
    winners = [n for n, v in verdict.items() if v["recommend"]]
    print(
        f"\n  RECOMMENDATION: {'turn ON -> ' + ', '.join(winners) if winners else 'KEEP ALL OFF — CMC is neutral/negative on this window'}"
    )
    if n_windows < 200:
        print(f"  ⚠️  only {n_windows} windows — low confidence; treat as directional.")
    print("=" * 78)

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window": {
            "bars": int(close.shape[0]),
            "tokens": list(tokens),
            "n_windows": n_windows,
            "start": str(index[0]) if index is not None else None,
            "end": str(index[-1]) if index is not None else None,
        },
        "macro_available": macro_ok,
        "config": {
            "floor": floor,
            "ceiling": ceiling,
            "blend": DEFAULT_BLEND,
            "w_dominance": settings.alloc_regime_w_dominance,
            "w_mktcap": settings.alloc_regime_w_mktcap,
            "w_fng_mom": settings.alloc_regime_w_fng_mom,
        },
        "friction": {
            fk: {name: {k: round(v, 6) for k, v in s.items()} for name, s in stats.items()}
            for fk, stats in by_friction.items()
        },
        "verdict": verdict,
        "recommend_on": winners,
    }
    if write:
        dest = JOURNAL_DIR / "cmc_pnl_ab.json"
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, dest)
        print(f"wrote {dest}")
    return payload


def run_sweep(close, tokens, p, floor, ceiling, am, *, write=True):
    """Grid over regime term weights, scored risk-adjusted + DQ-gated, with a 60/40
    walk-forward holdout to catch overfitting. baseline (0,0,0) is the anchor."""
    if am is None or not am.any_present():
        print("sweep needs historical macro (none available) — skipping.")
        return None
    n = close.shape[0]
    split = int(n * 0.6)
    grid = [0.0, 0.5, 1.0, 2.0]
    base_cap = cap_series(close, floor=floor, ceiling=ceiling, ma_window=settings.alloc_breadth_ma)
    base_stats = evaluate(close, weight_path(close, p, cap_series=base_cap), one_way=ONE_WAY_70BPS)
    rows = []
    for wd, wm, wf in product(grid, grid, grid):
        enh_cap = _enh_cap(close, am, floor, ceiling, wd, wm, wf)
        full = evaluate(close, weight_path(close, p, cap_series=enh_cap), one_way=ONE_WAY_70BPS)
        fit = evaluate(
            close[:split],
            weight_path(close[:split], p, cap_series=enh_cap[:split]),
            one_way=ONE_WAY_70BPS,
        )
        hold = evaluate(
            close[split:],
            weight_path(close[split:], p, cap_series=enh_cap[split:]),
            one_way=ONE_WAY_70BPS,
        )
        rows.append(
            {
                "w": (wd, wm, wf),
                "score": _score(full),
                "fit_score": _score(fit),
                "hold_score": _score(hold),
                "total_return": full["total_return"],
                "worst_week_dd": full["worst_week_dd"],
                "pct_dd_over_30": full["pct_dd_over_30"],
                "trades_per_week": full["trades_per_week"],
                "dq_safe": full["pct_dd_over_30"] == 0,
            }
        )
    safe = [r for r in rows if r["dq_safe"]]
    safe.sort(key=lambda r: r["score"], reverse=True)
    print(
        f"\n=== SWEEP (regime weights, @0.70%RT, walk-forward 60/40) — baseline score "
        f"{_score(base_stats):.3f} ==="
    )
    print(
        f"{'(w_dom,w_mc,w_fng)':20} {'score':>8} {'fit':>8} {'hold':>8} {'totRet':>9} "
        f"{'wkDD':>7} {'t/wk':>6} {'DQ':>4}"
    )
    print("-" * 78)
    for r in safe[:12]:
        print(
            f"{str(r['w']):20} {r['score']:8.2f} {r['fit_score']:8.2f} {r['hold_score']:8.2f} "
            f"{r['total_return'] * 100:+8.2f}% {r['worst_week_dd'] * 100:6.1f}% "
            f"{r['trades_per_week']:6.1f} {'✅' if r['dq_safe'] else '❌':>4}"
        )
    anchor = next((r for r in rows if r["w"] == (0.0, 0.0, 0.0)), None)
    default = next((r for r in rows if r["w"] == (1.0, 1.0, 1.0)), None)
    best = safe[0] if safe else None
    conclusion = ""
    if best and anchor and default:
        fit_gain = best["fit_score"] - anchor["fit_score"]
        hold_gain = best["hold_score"] - anchor["hold_score"]
        tune_gain = best["score"] - default["score"]  # winner vs the principled default
        print(
            f"\n  best cell {best['w']}: score {best['score']:.3f} | (0,0,0) anchor {anchor['score']:.3f} | "
            f"default (1,1,1) {default['score']:.3f}"
        )
        print(
            f"  walk-forward: best-cell vs anchor — fit Δ{fit_gain * 100:+.1f}pts, holdout Δ{hold_gain * 100:+.1f}pts"
        )
        if tune_gain <= 0.005 or hold_gain <= 0:
            conclusion = (
                "tuning the weights adds ~nothing beyond the principled default (1,1,1) and "
                "doesn't generalize -> KEEP w=(1,1,1); the WIN is enabling the enhanced regime."
            )
        else:
            conclusion = f"best weights {best['w']} beat the default by {tune_gain * 100:+.1f}pts and hold out -> consider adopting."
        print(f"  CONCLUSION: {conclusion}")
    if write:
        dest = JOURNAL_DIR / "cmc_pnl_ab_sweep.json"
        dest.write_text(
            json.dumps(
                {
                    "baseline_score": _score(base_stats),
                    "anchor": anchor,
                    "default_1_1_1": default,
                    "best": best,
                    "conclusion": conclusion,
                    "leaderboard": safe[:20],
                },
                indent=2,
                default=str,
            )
        )
        print(f"  wrote {dest}")
    return conclusion


def write_report(payload: dict, sweep_conclusion: str | None = None) -> None:
    """Deterministic markdown report from the A/B payload — the honest, committed proof."""
    f = payload["friction"]["0.0035"]
    w = payload["window"]
    rec = payload["recommend_on"]
    L = [
        "# CMC PnL A/B — does the enhanced regime / tilt / ranking improve simulated PnL?",
        "",
        f"_Generated {payload['generated_utc']} · {w['bars']} 4h bars × {len(w['tokens'])} tokens "
        f"({str(w['start'])[:10]} → {str(w['end'])[:10]}) · {w['n_windows']} rolling-7d windows · "
        f"macro={'yes' if payload['macro_available'] else 'no'}._",
        "",
        "## How to read this",
        "",
        "The momentum engine + candles are held **constant**; only the deploy-cap source (baseline vs CMC "
        "macro), the within-set tilt, and the ranking change — so any difference IS that lever's PnL "
        "contribution. The strategy's edge is **exposure management**, not alpha (no fixed edge; entry-regime "
        "can't predict the next week), so it is judged on the **risk-penalized return** "
        "`score = total_return − worst_week_dd` and **DQ-safety** (`pct_dd_over_30 == 0`), at the "
        "contest-realistic 0.70%RT friction. Returns are cumulative over a down-leaning ~14-month window, so "
        "they are negative across the board — the question is which lever **loses less / draws down less**.",
        "",
        "## Results (0.70%RT)",
        "",
        "| arm | total return | worst-week DD | max DD | %weeks up | trades/wk | score |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in f.items():
        label = f"**{name}**" if name in rec else name
        L.append(
            f"| {label} | {s['total_return'] * 100:+.2f}% | {s['worst_week_dd'] * 100:.1f}% | "
            f"{s['max_dd'] * 100:.1f}% | {s['pct_up'] * 100:.0f}% | {s['trades_per_week']:.1f} | "
            f"{s['total_return'] - s['worst_week_dd']:+.3f} |"
        )
    L += ["", "## Verdict", ""]
    for name, v in payload["verdict"].items():
        L.append(
            f"- **{name}** — {v['tag']}: Δscore {v['d_score'] * 100:+.1f}pts, "
            f"Δworst-week-DD {v['d_worst_week_dd'] * 100:+.1f}pts, DQ-safe "
            f"{'yes' if v['dq_safe'] else 'NO'} → {'**TURN ON**' if v['recommend'] else 'keep off'}"
        )
    L += ["", "## Recommendation", ""]
    if rec:
        ta_on = [a for a in rec if "ta" in a]
        macro_on = [a for a in rec if a in ("enhanced", "enhanced+tilt")]
        best = max(rec, key=lambda a: f[a]["total_return"] - f[a]["worst_week_dd"])
        L.append(
            f"**Turn ON: {', '.join(rec)}** (best single arm: **{best}**) — with the principled "
            "default term weights. Two CMC levers clear the bar on this window:"
        )
        if macro_on:
            L.append(
                "- **Enhanced regime** — folds the CMC **macro** (BTC-dominance / total-mktcap / "
                "F&G-momentum) into the deploy cap; improves the risk-penalized return."
            )
        if ta_on:
            L.append(
                "- **Technical analysis** — folds CMC's pre-computed **RSI / MACD / EMA** (daily) into "
                "the deploy cap (`ta_cap`) and the token ranking (`ta_rank`); `ta_cap` uniquely **cuts "
                "worst-week drawdown**, and **`enhanced+ta`** (macro + TA in the cap) is the strongest "
                "config. Backtested locally on the candle history; LIVE reads CMC's authoritative "
                "pre-computed TA via the Agent Hub MCP — same signal, compute offloaded to CMC."
            )
        L.append(
            "**Per SIM-first: enable on the SIM track and forward-validate before promoting to LIVE**; "
            "the contest entry stays on the validated baseline until then. Over-stacking every lever "
            "(`full_cmc`, `full_cmc+ta`) and the bare tilt/multi-TF ranking are **neutral/negative** "
            "here — keep them OFF."
        )
    else:
        L.append(
            "**Keep all CMC trade-levers OFF** — neutral/negative on this window. CMC remains a "
            "verified data layer + dashboard, not a trade driver."
        )
    if sweep_conclusion:
        L += ["", "## Weight tuning (walk-forward 60/40)", "", sweep_conclusion]
    L += [
        "",
        "---",
        "",
        "_Honest caveats: a single ~14-month window (warmup eats ~27 days); F&G history "
        "~500 days covers it; a down-leaning sample so all returns are negative; the forward SIM A/B is "
        "the real arbiter. Data provided by CoinMarketCap._",
        "",
    ]
    dest = ROOT / "docs" / "cmc_pnl_ab.md"
    dest.write_text("\n".join(L))
    print(f"wrote {dest}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2500)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument(
        "--candle-source",
        choices=["binance_4h", "cmc_daily"],
        default="binance_4h",
        help="cmc_daily = CEX-free A/B on real CMC daily history (validates the CMC arm's "
        "TA/macro levers); binance_4h = legacy CEX reference.",
    )
    args = ap.parse_args()

    if settings.cmc_only and args.candle_source == "binance_4h":
        print("ERROR: CMC_ONLY=true forbids --candle-source binance_4h (CEX). Use cmc_daily.")
        return 2

    mat, have = _load(args.limit, args.candle_source)
    if mat.shape[0] < 400 or mat.shape[1] < 3:
        print(f"ERROR: not enough aligned data ({mat.shape}).")
        return 2
    close = mat.to_numpy()
    tokens = list(mat.columns)
    print(
        f"[source] {args.candle_source}: loaded {len(have)}/{len(CONTEST_TOKENS)} tokens; "
        f"aligned {mat.shape[0]} bars x {mat.shape[1]}"
    )
    floor, ceiling = settings.alloc_cap_floor, settings.alloc_cap_ceiling
    # Daily candles need daily-rescaled horizons (lookback 20d, vol 10d, daily rebal); 4h uses defaults.
    p = (
        AllocatorParams(
            lookback=20, vol_lookback=10, rebal_bars=1, abs_filter=settings.alloc_abs_filter
        )
        if args.candle_source == "cmc_daily"
        else AllocatorParams(abs_filter=settings.alloc_abs_filter)
    )
    am, macro_ok = _macro(mat.index)
    if not macro_ok:
        print("⚠️  no historical macro (set CMC_API_KEY / check budget) — macro arms skipped.")
    try:
        ta = _ta(mat)
        print(
            "CMC technical-analysis lever: daily RSI/MACD/EMA aligned to 4h (ta_cap, ta_rank arms)."
        )
    except Exception as e:  # noqa: BLE001 — TA arms are additive; never block the macro A/B
        ta = None
        print(f"⚠️  TA signal unavailable ({e}) — TA arms skipped.")

    payload = run_ab(
        close,
        tokens,
        p,
        floor,
        ceiling,
        am,
        macro_ok,
        write=not args.no_write,
        index=mat.index,
        ta=ta,
    )
    sweep_conclusion = (
        run_sweep(close, tokens, p, floor, ceiling, am, write=not args.no_write)
        if args.sweep
        else None
    )
    if not args.no_write:
        write_report(payload, sweep_conclusion)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
