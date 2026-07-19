"""
The single, shared Gate-A acceptance gate.

Gate-A logic was copy-pasted across ``scripts/validate_allocator.py`` (portfolio:
worst-week DD < 30%, >= 7 trades/wk) and ``scripts/validate_trend.py`` (per-pair WFO:
TRAIN>0 & TEST>0 & >= min closures, plus a basket holders+trades rule). This module
centralizes the verdict as DATA (``GateThresholds``) + small evaluators so every
strategy — the locked allocator and every new one — is judged the same way. The
defaults reproduce the existing inline literals, so wiring the old scripts to
delegate here changes no exit code.

FRICTION DISCIPLINE: allocation strategies must be scored through
``engine.portfolio_replay.evaluate`` at ``ONE_WAY_70BPS`` (the binding ~0.70%
spot-DEX round-trip); per-pair signal strategies through ``engine.wfo_replay``. Never
mix the two friction models — see docs/strategy_playbook.md §8.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ictbot.engine.wfo import classify

HOLD = "✅ holds"


@dataclass(frozen=True)
class GateThresholds:
    # Drawdown budget: a strategy may USE up to `max_worst_week_dd` of worst-week
    # drawdown but must never move beyond it. This 25% operational ceiling sits
    # safely inside the 30% contest DQ line (`dq_line`, informational) and above the
    # 15% stretch target (`target_worst_week_dd`). It is the pass/fail rail.
    max_worst_week_dd: float = 0.25  # HARD operational ceiling (acceptance pass/fail)
    dq_line: float = 0.30  # the contest disqualification line (informational)
    target_worst_week_dd: float = 0.15  # the stretch target (informational)
    min_trades_per_week: float = 7.0  # the contest min-trade floor
    min_train_exp: float = 0.0  # WFO: TRAIN > 0
    min_test_exp: float = 0.0  # WFO: TEST > 0
    min_closures: int = 10  # WFO statistical-significance floor
    min_holders: int = 2  # basket: >= 2 assets hold


DEFAULT = GateThresholds()


@dataclass(frozen=True)
class GateResult:
    passed: bool
    dq_safe: bool
    active: bool
    reasons: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)


def evaluate_portfolio(stats: dict, t: GateThresholds = DEFAULT) -> GateResult:
    """Verdict for an allocation strategy from a ``portfolio_replay.evaluate`` dict.

    ``dq_safe`` = worst-week DD within the 25% operational ceiling (the hard rail);
    ``active`` = clears the >= 7 trades/week floor; ``passed`` = both.
    ``metrics.within_dq_line`` flags the 30% contest DQ and ``metrics.target_dd_met``
    the 15% stretch target (both informational, not part of pass/fail).
    """
    wdd = float(stats.get("worst_week_dd", 1.0))
    tpw = float(stats.get("trades_per_week", 0.0))
    dq_safe = wdd < t.max_worst_week_dd
    active = tpw >= t.min_trades_per_week
    reasons = []
    if not dq_safe:
        reasons.append(f"worst-week DD {wdd:.1%} >= {t.max_worst_week_dd:.0%} ceiling")
    if not active:
        reasons.append(f"trades/week {tpw:.1f} < floor {t.min_trades_per_week:.0f}")
    return GateResult(
        passed=dq_safe and active,
        dq_safe=dq_safe,
        active=active,
        reasons=tuple(reasons),
        metrics={
            "worst_week_dd": wdd,
            "trades_per_week": tpw,
            "within_dq_line": wdd < t.dq_line,
            "target_dd_met": wdd < t.target_worst_week_dd,
        },
    )


def evaluate_walk_forward(res: dict, t: GateThresholds = DEFAULT) -> GateResult:
    """Verdict for a single per-pair signal from a ``wfo_replay.walk_forward`` dict
    (reuses ``wfo.classify``). ``dq_safe`` here = worst rolling-7d DD under target."""
    verdict = classify(
        res.get("train_exp"), res.get("test_exp"), res.get("test_closures"), t.min_closures
    )
    holds = verdict == HOLD
    dd = res.get("worst_7d_dd")
    dq_safe = dd is not None and dd < t.target_worst_week_dd
    reasons = []
    if not holds:
        reasons.append(f"WFO verdict: {verdict}")
    if not dq_safe:
        shown = "n/a" if dd is None else f"{dd:.1%}"
        reasons.append(f"worst 7d DD {shown} >= target {t.target_worst_week_dd:.0%}")
    return GateResult(
        passed=holds and dq_safe,
        dq_safe=dq_safe,
        active=holds,
        reasons=tuple(reasons),
        metrics={"verdict": verdict, "worst_7d_dd": dd},
    )


def evaluate_basket(per_pair: dict, basket_tpw: float, t: GateThresholds = DEFAULT) -> GateResult:
    """validate_trend.py's portfolio rule: >= ``min_holders`` assets that 'hold' with
    7d DD < target, AND the deployable basket clears the weekly trade floor.

    ``per_pair`` maps symbol -> a ``walk_forward`` result dict. ``metrics.holders`` is
    the surviving set (so the caller can print it without recomputing).
    """
    holders = [
        s
        for s, r in per_pair.items()
        if r.get("verdict") == HOLD
        and r.get("worst_7d_dd") is not None
        and r["worst_7d_dd"] < t.target_worst_week_dd
    ]
    enough_edge = len(holders) >= t.min_holders
    enough_trades = basket_tpw >= t.min_trades_per_week
    reasons = []
    if not enough_edge:
        reasons.append(f"{len(holders)} holders < {t.min_holders}")
    if not enough_trades:
        reasons.append(f"basket {basket_tpw:.1f} t/wk < {t.min_trades_per_week:.0f}")
    return GateResult(
        passed=enough_edge and enough_trades,
        dq_safe=enough_edge,
        active=enough_trades,
        reasons=tuple(reasons),
        metrics={"holders": tuple(holders), "basket_tpw": basket_tpw},
    )
