"""
Phase 14.D — Edge reality check (statistical).

Reads `data/journal/signals.json`, filters to broker-truth closes,
and per pair reports:

  * n              — sample size
  * mean R         — realised expectancy
  * std R          — sample standard deviation
  * sum R          — cumulative R
  * t vs 0         — t-statistic against the null "no edge"
  * t vs WFO TEST  — t-statistic against the WFO TEST expectancy
                    from data/wfo/per_pair_<date>.json (or the
                    Phase 9.A scoreboard hardcoded as the default
                    baseline when no JSON is available)
  * verdict        — one of:
                     "insufficient data (n<10)"
                     "no signal yet (n<30 or not significant)"
                     "real edge — matches WFO"
                     "real edge — exceeds WFO"
                     "real edge — below WFO"
                     "no edge — strategy break-even"
                     "negative edge — losing money"

Statistical model:
  * One-sample t-test, two-sided. t = (mean − μ₀) / (s/√n).
  * p-values are normal-approximation (valid at n≥30; the CLT
    kicks in and Student's t → standard normal). Below n=30 we
    report t but not p — operator interprets with the usual
    rule of thumb (|t| > 2 ≈ 5% significant for n>10).
  * Verdict thresholds: a pair needs n≥30 AND |t vs 0| > 2 AND
    mean R > 0 to be flagged as "real edge". Otherwise it's
    "no signal yet" or "no edge" depending on direction.

USAGE:
  python3 scripts/edge_check.py                       # pretty-printed
  python3 scripts/edge_check.py --json                # machine-readable
  python3 scripts/edge_check.py --min-n 20            # lower the bar
  python3 scripts/edge_check.py --wfo path/to.json    # override baseline

Or via make:
  make edge_check
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from statistics import mean, stdev

REPO_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = REPO_ROOT / "data" / "journal" / "signals.json"

# Phase 9.A scoreboard (2026-06-06 rr2plus, 10k bars). Used when no
# per-pair JSON is available. Source: data/wfo/per_pair_2026-06-06.txt
# bottom-of-file SCOREBOARD block.
DEFAULT_WFO_TEST_EXPECTANCY = {
    "BTC/USDT:USDT": 0.09,  # small sample (1/5)
    "ETH/USDT:USDT": 0.45,  # ✅ holds (8/17)
    "SOL/USDT:USDT": 0.80,  # ✅ holds (17/28)
    "XRP/USDT:USDT": 0.88,  # small sample (4/4)
    # PAXG dropped in Phase 11 — `no edge` (TRAIN -0.85R) so the
    # live baseline isn't a meaningful comparison even if rows exist.
}

# Default minimum sample size before we claim anything statistically.
DEFAULT_MIN_N = 30


def _read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _filter_broker_truth(rows: list[dict]) -> list[dict]:
    """Filter to broker-truth closed rows (broker != paper AND
    pnl_r is populated AND outcome is a terminal state).

    Mirrors the classifier in scripts/diagnose_live_pnl.py."""
    out = []
    for r in rows:
        outcome = r.get("outcome")
        if outcome not in ("WIN", "LOSS", "BE", "CLOSED"):
            continue
        broker = r.get("broker", "paper")
        if broker in (None, "paper"):
            continue
        pnl_r = r.get("pnl_r")
        if pnl_r is None:
            continue
        out.append(r)
    return out


def _load_wfo_baseline(path: Path | None) -> dict[str, float]:
    """Read a per_pair_<date>.json WFO file → {pair: TEST exp R}.

    Falls back to the hardcoded scoreboard when no path is given or
    the file doesn't have the expected shape."""
    if path is None or not path.exists():
        return dict(DEFAULT_WFO_TEST_EXPECTANCY)
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_WFO_TEST_EXPECTANCY)

    out = {}
    pairs = data.get("pairs") or {}
    for pair, pair_data in pairs.items():
        if not isinstance(pair_data, dict):
            continue
        test_block = pair_data.get("test")
        if not isinstance(test_block, dict):
            continue
        exp = test_block.get("expectancy_R")
        if isinstance(exp, (int, float)):
            out[pair] = float(exp)
    # If parsing yielded nothing usable, fall back rather than mislead.
    return out or dict(DEFAULT_WFO_TEST_EXPECTANCY)


def _t_stat(sample: list[float], mu0: float) -> float | None:
    """One-sample t-statistic: (mean − μ₀) / (s/√n).

    Returns None if sample is too small (n<2) or has zero variance."""
    n = len(sample)
    if n < 2:
        return None
    m = mean(sample)
    s = stdev(sample)
    if s == 0:
        return None
    return (m - mu0) / (s / math.sqrt(n))


def _normal_p_two_sided(t: float | None) -> float | None:
    """Normal-approximation two-sided p-value. Valid for n≥30 where
    CLT kicks in. Below that the t-distribution has heavier tails
    and this OVERSTATES significance — caller decides whether to
    report it."""
    if t is None:
        return None
    # P(|Z| > |t|) = 2 * (1 - Phi(|t|)) = 2 * Phi(-|t|)
    return math.erfc(abs(t) / math.sqrt(2))


def _verdict(
    n: int, mean_r: float, t_vs_zero: float | None, min_n: int
) -> str:
    """Translate the stats into a one-line verdict.

    Two thresholds:
      * n >= min_n  : enough data to claim anything statistically
      * |t| > 2     : two-sided 5% significance (normal-approx)
    """
    if n < min_n // 3:  # < 10 by default
        return "insufficient data — wait for more closes"
    if n < min_n:
        return "no signal yet — sample too small"
    if t_vs_zero is None:
        return "insufficient data — zero variance"
    if abs(t_vs_zero) <= 2.0:
        return "no edge — strategy near break-even"
    if t_vs_zero > 2.0 and mean_r > 0:
        return "REAL EDGE — mean R > 0 significant"
    if t_vs_zero < -2.0 and mean_r < 0:
        return "NEGATIVE EDGE — strategy is losing"
    return "ambiguous — check raw numbers"


def _decorate_with_wfo(
    pair: str, mean_r: float, t_vs_zero: float | None,
    sample: list[float], wfo_exp: float | None
) -> str | None:
    """If the WFO TEST expectation is available AND the pair has a
    real edge, add a tag noting whether live matches / exceeds / is
    below the WFO snapshot. Returns None when we can't make the
    call."""
    if wfo_exp is None or t_vs_zero is None or abs(t_vs_zero) <= 2.0:
        return None
    if mean_r <= 0:
        return None
    # Test if live mean is within 0.3R of WFO (heuristic close-enough
    # band; tighter than the std of typical 1:5 RR R-multiples).
    delta = mean_r - wfo_exp
    if abs(delta) < 0.3:
        return "matches WFO"
    if delta > 0:
        return "exceeds WFO"
    return "below WFO"


def compute_per_pair(rows: list[dict]) -> dict[str, dict]:
    """Aggregate rows by pair → stats dict."""
    by_pair: dict[str, list[float]] = {}
    for r in rows:
        p = r.get("pair") or "?"
        by_pair.setdefault(p, []).append(float(r["pnl_r"]))
    out = {}
    for pair, sample in by_pair.items():
        out[pair] = {
            "n": len(sample),
            "mean_r": mean(sample) if sample else 0.0,
            "std_r": stdev(sample) if len(sample) > 1 else 0.0,
            "sum_r": sum(sample),
        }
    return out


def build_report(
    rows: list[dict],
    *,
    wfo_baseline: dict[str, float],
    min_n: int = DEFAULT_MIN_N,
) -> dict:
    """Compute the full edge-check report."""
    broker_truth = _filter_broker_truth(rows)
    per_pair_stats = compute_per_pair(broker_truth)
    by_pair_samples: dict[str, list[float]] = {}
    for r in broker_truth:
        by_pair_samples.setdefault(r["pair"], []).append(float(r["pnl_r"]))

    per_pair: dict[str, dict] = {}
    for pair, stats in per_pair_stats.items():
        sample = by_pair_samples[pair]
        wfo_exp = wfo_baseline.get(pair)
        t_vs_zero = _t_stat(sample, 0.0)
        t_vs_wfo = _t_stat(sample, wfo_exp) if wfo_exp is not None else None
        p_vs_zero = _normal_p_two_sided(t_vs_zero) if stats["n"] >= min_n else None
        p_vs_wfo = _normal_p_two_sided(t_vs_wfo) if (
            wfo_exp is not None and stats["n"] >= min_n
        ) else None
        verdict = _verdict(stats["n"], stats["mean_r"], t_vs_zero, min_n)
        wfo_tag = _decorate_with_wfo(
            pair, stats["mean_r"], t_vs_zero, sample, wfo_exp
        )
        if wfo_tag:
            verdict = f"REAL EDGE — {wfo_tag}"
        per_pair[pair] = {
            **stats,
            "wfo_test_exp_r": wfo_exp,
            "t_vs_zero": t_vs_zero,
            "p_vs_zero": p_vs_zero,
            "t_vs_wfo": t_vs_wfo,
            "p_vs_wfo": p_vs_wfo,
            "verdict": verdict,
        }

    # Overall aggregate across all broker-truth closes
    all_samples = [float(r["pnl_r"]) for r in broker_truth]
    overall = {
        "n": len(all_samples),
        "mean_r": mean(all_samples) if all_samples else 0.0,
        "sum_r": sum(all_samples),
    }

    return {
        "min_n": min_n,
        "per_pair": per_pair,
        "overall": overall,
        "broker_truth_count": len(broker_truth),
    }


def _fmt_t(t: float | None) -> str:
    return f"{t:+.2f}" if t is not None else "n/a"


def _fmt_p(p: float | None) -> str:
    if p is None:
        return "n/a"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _fmt_r(r: float | None) -> str:
    return f"{r:+.3f}" if r is not None else "n/a"


def _print_human(report: dict) -> None:
    n_truth = report["broker_truth_count"]
    print("=" * 96)
    print(f"edge_check  (broker-truth closes: {n_truth}, "
          f"min_n for significance: {report['min_n']})")
    print("=" * 96)

    if n_truth == 0:
        print("\nNo broker-truth closes in the journal yet. Run the scanner "
              "first, then re-check.")
        return

    headers = (
        f"{'pair':<22}{'n':>5}{'mean R':>10}{'std':>8}{'sum R':>10}"
        f"{'WFO TEST':>10}{'t vs 0':>10}{'p vs 0':>10}"
    )
    print(headers)
    print("-" * len(headers))
    for pair, s in sorted(report["per_pair"].items()):
        wfo_s = _fmt_r(s["wfo_test_exp_r"])
        print(
            f"{pair:<22}{s['n']:>5}{s['mean_r']:>+10.3f}{s['std_r']:>8.3f}"
            f"{s['sum_r']:>+10.3f}{wfo_s:>10}"
            f"{_fmt_t(s['t_vs_zero']):>10}{_fmt_p(s['p_vs_zero']):>10}"
        )

    print()
    print("Per-pair verdicts:")
    for pair, s in sorted(report["per_pair"].items()):
        print(f"  {pair:<22} {s['verdict']}")

    o = report["overall"]
    print()
    print(f"Overall: n={o['n']}  mean R={o['mean_r']:+.3f}  "
          f"sum R={o['sum_r']:+.3f}")
    print()
    print("Verdict guide:")
    print("  insufficient data    n < min_n/3        (whatever you see is noise)")
    print("  no signal yet        n < min_n          (need more closes)")
    print("  no edge              |t vs 0| ≤ 2        (≈ break-even)")
    print("  REAL EDGE            t vs 0 > 2, mean>0  (statistically positive)")
    print("  NEGATIVE EDGE        t vs 0 < -2         (statistically negative)")
    print("  matches/exceeds/below WFO: realised vs scoreboard delta")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument(
        "--journal", default=str(JOURNAL_PATH),
        help=f"Journal path (default: {JOURNAL_PATH})",
    )
    ap.add_argument(
        "--wfo", default=None,
        help="WFO baseline JSON (default: hardcoded Phase 9.A scoreboard)",
    )
    ap.add_argument(
        "--min-n", type=int, default=DEFAULT_MIN_N,
        help=f"Minimum sample size for significance (default {DEFAULT_MIN_N})",
    )
    ap.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of pretty-printed table.",
    )
    args = ap.parse_args()

    journal_path = Path(args.journal)
    rows = _read_journal(journal_path)

    wfo_baseline = _load_wfo_baseline(
        Path(args.wfo) if args.wfo else None
    )

    report = build_report(rows, wfo_baseline=wfo_baseline, min_n=args.min_n)
    report["journal_path"] = str(journal_path)
    report["wfo_baseline"] = wfo_baseline

    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_human(report)

    # Exit code:
    #   0 — at least one pair has REAL EDGE confirmed
    #   1 — overall pending (no pair has crossed the bar yet)
    #   2 — no broker-truth closes at all (infra / pre-restart)
    if report["broker_truth_count"] == 0:
        return 2
    if any("REAL EDGE" in s["verdict"] for s in report["per_pair"].values()):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
