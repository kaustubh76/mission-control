#!/usr/bin/env python3
"""
One-shot strategy validation campaign — wire EVERY registered arm through the gate
pipeline in a single command, persist its verdicts, and regenerate the comparison report
+ the guardian status matrix.

What it does, for every REAL arm (registry.available() minus the BNB_STRATEGY_0X aliases,
which delegate bit-for-bit to their target so validating the target covers them):

  1. BACKTEST SURVIVAL — run the shared Gate-A survival path (validate_strategy.survival_for:
     portfolio_replay.evaluate at the BINDING 0.70% spot-DEX friction + acceptance.evaluate_portfolio)
     and persist the `survival` verdict — the SAME payload `validate_strategy --save-verdict` writes.
  2. FORWARD READ — read the SIM journal and evaluate the Part-7 forward check
     (forward_promote._verdict_for) at the contest-compressed window, persisting the `forward`
     verdict — the SAME payload `forward_promote --save` writes. Arms with too little forward
     history read "insufficient forward data" (the honest common state in the contest window).
  3. ARTIFACTS — rewrite the GUARDIAN status matrix (between markers) in docs/strategy_campaign.md
     and the detailed comparison report data/reports/strategy_campaign.md, both regenerated from
     the verdicts. Ranked RISK-FIRST (survival pass, then lowest worst-week DD) — there is no
     proven long-only edge on this universe (docs/bnb_strategy_decision.md §1), so the
     differentiator is risk/turnover, not alpha.

CONTEST-SAFETY: read-only against the live world. It only runs backtests, READS the SIM journal,
and WRITES verdict JSON + docs. It never ticks the ledger, never changes the selected SIM arm, and
never touches the LIVE/contest path. The locked `momentum_adaptive` stays the bit-for-bit default;
promotion to LIVE always needs explicit operator sign-off (STRATEGY_NAME + ENABLE_LIVE_TRADING).

Usage:
  python scripts/strategy_campaign.py                      # full campaign, persist verdicts + docs
  python scripts/strategy_campaign.py --no-save            # dry run: print, don't persist/rewrite
  python scripts/strategy_campaign.py --forward-min-days 14  # use the rigorous forward window
  python scripts/strategy_campaign.py --limit 2500 --static-cap
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import scripts.forward_promote as fp
import scripts.validate_strategy as vs
from ictbot.api.reads import read_journal
from ictbot.runtime import performance, verdicts
from ictbot.settings import DATA_DIR
from ictbot.strategy import registry

# Guardian block markers — the campaign rewrites ONLY the text between these in the doc.
GUARDIAN_START = "<!-- GUARDIAN:START -->"
GUARDIAN_END = "<!-- GUARDIAN:END -->"

# Contest-compressed forward window: with ~8 days to the deadline a rigorous 14-day forward
# track can't exist for 8 arms, so the campaign judges forward on a shorter span by default.
# It is LABELLED as compressed everywhere it appears (guardian footer + report header).
DEFAULT_FORWARD_MIN_DAYS = 5.0

# The locked contest default — Stage 5 (operator sign-off). Everything else is a challenger.
SIGNED_OFF = {"momentum_adaptive"}

_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = _ROOT / "docs" / "strategy_campaign.md"
REPORT_PATH = DATA_DIR / "reports" / "strategy_campaign.md"


def real_arms() -> list[str]:
    """Registered arms excluding the BNB_STRATEGY_0X aliases (they delegate bit-for-bit)."""
    return [n for n in registry.available() if registry.alias_target(n) is None]


def alias_for(arm: str) -> str | None:
    """The first branded BNB_STRATEGY_0X name pointing at `arm`, if any."""
    for alias, target in registry.CONTEST_ALIASES.items():
        if target == arm:
            return alias
    return None


def _stage(*, survival_passed: bool, started: bool, forward_eligible: bool, signed_off: bool) -> int:
    """Pipeline stage for the guardian matrix (see docs/strategy_campaign.md §2).

    Stages 1–4 are progressive (each requires the previous). Stage 5 (operator sign-off) is
    the administrative live-default flag, layered on top for a signed-off arm that still
    clears THIS run's survival gate; it does NOT require forward-eligibility — live promotion
    is a manual operator decision (Part 7), not an auto-cleared rung. A signed-off arm that
    FAILS this run's survival gate keeps its real (lower) stage, with the FAIL visible in the
    survival column — so the matrix never shows Stage 5 next to a survival ❌.
    """
    stage = 1                                   # registered
    if survival_passed:
        stage = 2                               # backtest-survival
    if stage >= 2 and started:
        stage = 3                               # forward-started (has SIM journal rows)
    if stage >= 3 and forward_eligible:
        stage = 4                               # forward-eligible
    if signed_off and survival_passed:
        stage = 5                               # operator sign-off (manual; survival still required)
    return stage


def evaluate_arm(arm: str, journal: list[dict], *, limit: int, static_cap: bool,
                 forward_min_days: float, save: bool, now_iso: str,
                 frames: dict | None = None) -> dict:
    """Run survival + forward for one arm, optionally persisting both verdicts."""
    out: dict = {"arm": arm, "alias": alias_for(arm)}
    try:
        rep = vs.survival_for(arm, limit=limit, static_cap=static_cap, frames=frames)
    except KeyError as e:
        out["error"] = str(e)
        return out
    out["summary"] = rep.get("summary", "")
    if not rep.get("ok"):
        out["error"] = rep.get("error", "no data")
        return out

    gate, s = rep["gate"], rep["stats_70"]
    # Single source of truth for the survival payload (shared with validate_strategy
    # --save-verdict) so the two writers can never drift — see vs.survival_payload.
    survival = vs.survival_payload(gate, s, now_iso)
    forward = {**fp._verdict_for(journal, arm, min_days=forward_min_days), "ts": now_iso}
    rows_for_arm = fp._strategy_rows(journal, arm)
    started = len(rows_for_arm) > 0
    # Performance SCOREBOARD (distinct from the survival GATE, persisted under the "perf" kind):
    # backtest PnL + WINDOW win-rate, already computed by portfolio_replay.evaluate (no new
    # backtest), plus forward net + DAY win-rate from THIS arm's journal track. Ranked in the
    # report, never gated on — there is no edge claim (see render_scoreboard's honest header).
    bt = performance.backtest_perf(s)
    fwd_perf = performance.forward_perf([(dt.isoformat(), nav) for dt, nav, _ in rows_for_arm])

    out.update(
        survival=survival,
        forward=forward,
        started=started,
        mean_ret=s.get("mean_ret"),
        median_ret=s.get("median_ret"),
        total_return=bt["total_return"],
        win_rate=bt["win_rate"],
        fwd_perf=fwd_perf,
        stage=_stage(survival_passed=survival["passed"], started=started,
                     forward_eligible=bool(forward.get("forward_eligible")),
                     signed_off=arm in SIGNED_OFF),
    )
    if save:
        verdicts.record(arm, "survival", survival)
        verdicts.record(arm, "forward", forward)
        verdicts.record(arm, "perf", {**bt, "ts": now_iso})
    return out


def _rank_key(r: dict):
    """Risk-first: errored arms last, then survival passers, then lowest worst-week DD."""
    if r.get("error") or not r.get("survival"):
        return (2, 1.0, r["arm"])
    return (0 if r["survival"]["passed"] else 1, r["survival"]["worst_week_dd"], r["arm"])


def _perf_key(r: dict):
    """Scoreboard: survival passers first, then HIGHEST backtest total-return (None last).

    Equity can't fall below 0 so total_return >= -1.0; the None sentinel (+1.0) therefore always
    sorts after any real return. Ascending sort on the negated return ⇒ best PnL on top.
    """
    sv = r.get("survival")
    if r.get("error") or not sv:
        return (2, 1.0, r["arm"])
    tr = r.get("total_return")
    return (0 if sv["passed"] else 1, -(tr if tr is not None else -1.0), r["arm"])


def _pct(x, places: int = 1, *, signed: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x * 100:{'+' if signed else ''}.{places}f}%"


def _fwd_net_cell(fp_perf: dict | None) -> str:
    if not fp_perf or fp_perf.get("status") in (None, "none"):
        return "—"
    return _pct(fp_perf.get("net_pct"), 2, signed=True)


def _fwd_winrate_cell(fp_perf: dict | None) -> str:
    if not fp_perf or fp_perf.get("status") != "evaluated" or fp_perf.get("win_rate") is None:
        return "⏳ accruing" if (fp_perf or {}).get("status") == "accruing" else "—"
    return f"{fp_perf['win_rate'] * 100:.0f}% ({fp_perf['wins']}/{fp_perf['decided']}d)"


def render_scoreboard(ranked: list[dict]) -> list[str]:
    """PnL / win-rate SCOREBOARD over the survivors — the performance view layered on the gate.

    Honest by construction: ranked by backtest total-return, but headed with the no-edge caveat so
    nobody reads the #1 row as alpha. Two DISTINCT win-rates are shown and labelled — WINDOW
    (backtest: share of rolling 7-day windows positive) and DAY (forward: live up-days/decided,
    matches the dashboard `pnl.ts`).
    """
    rows = [r for r in sorted(ranked, key=_perf_key) if r.get("survival")]
    out = [
        "## PnL / win-rate scoreboard",
        "",
        "_Performance among the arms that **clear the survival gate**, ranked by backtest "
        "total-return. This is a **scoreboard, not an edge claim**: over the long backtest sample "
        "the ranking is dominated by how much each arm rode the trending regime, not a repeatable "
        "edge (decision-record §1). The contest-length **window** win-rate (share of rolling 7-day "
        "windows positive) and the forward **day** win-rate (live up-days, matches the dashboard) "
        "are the more decision-relevant numbers; forward fills in over wall-clock days._",
        "",
        "| # | Arm | Survival | Backtest total-return | Win-rate (window) | Forward net % | "
        "Win-rate (day) |",
        "|--:|---|:--:|--:|--:|--:|--:|",
    ]
    for i, r in enumerate(rows, 1):
        out.append(
            f"| {i} | `{r['arm']}` | {_survival_cell(r['survival'])} | "
            f"{_pct(r.get('total_return'), 1, signed=True)} | {_pct(r.get('win_rate'), 0)} | "
            f"{_fwd_net_cell(r.get('fwd_perf'))} | {_fwd_winrate_cell(r.get('fwd_perf'))} |"
        )
    out.append("")
    return out


def _forward_cell(forward: dict | None) -> str:
    if not forward:
        return "—"
    if forward.get("status") != "evaluated":
        return "⏳ insufficient"
    return "✅ eligible" if forward.get("forward_eligible") else "❌ not yet"


def _survival_cell(survival: dict | None) -> str:
    if not survival:
        return "—"
    dd = survival["worst_week_dd"] * 100
    return f"{'✅' if survival['passed'] else '❌'} {dd:.1f}% DD"


def render_guardian(results: list[dict], *, forward_min_days: float, now_iso: str) -> str:
    """The auto-regenerated guardian status matrix (full block incl. the markers)."""
    ranked = sorted(results, key=_rank_key)
    lines = [
        GUARDIAN_START,
        "",
        f"_Auto-generated by `make campaign` — do not hand-edit between the markers. "
        f"Forward window **{forward_min_days:g}d** (contest-compressed; the rigorous default is "
        f"14d). Last run **{now_iso}**._",
        "",
        "| # | Arm | Alias | Stage | Backtest-survival | t/wk | Forward |",
        "|--:|---|---|:--:|---|--:|---|",
    ]
    for i, r in enumerate(ranked, 1):
        alias = r.get("alias") or "—"
        if r.get("error"):
            lines.append(f"| {i} | `{r['arm']}` | {alias} | — | ⚠️ {r['error']} | — | — |")
            continue
        sv, fw = r.get("survival"), r.get("forward")
        tpw = f"{sv['trades_per_week']:.1f}" if sv else "—"
        lines.append(
            f"| {i} | `{r['arm']}` | {alias} | {r.get('stage', 1)} | "
            f"{_survival_cell(sv)} | {tpw} | {_forward_cell(fw)} |"
        )
    lines += [
        "",
        "Stages: **1** registered · **2** backtest-survival · **3** forward-started · "
        "**4** forward-eligible · **5** operator sign-off (the live default, set manually — "
        "requires survival but not forward; live promotion is a manual decision, Part 7). "
        "Only `momentum_adaptive` is at Stage 5, and only while it still clears survival.",
        "",
        GUARDIAN_END,
    ]
    return "\n".join(lines)


def splice_guardian(doc_text: str, block: str) -> str:
    """Replace the existing GUARDIAN block (inclusive of markers) with `block`; if no
    markers are present, append the block to the end."""
    pat = re.compile(re.escape(GUARDIAN_START) + r".*?" + re.escape(GUARDIAN_END), re.DOTALL)
    if pat.search(doc_text):
        # count=1: replace only the FIRST marker pair (a stray/duplicated second pair from a
        # hand-edit or merge would otherwise get the matrix written into it too). The lambda
        # replacement inserts `block` verbatim — no regex backref interpretation of its content.
        return pat.sub(lambda _m: block, doc_text, count=1)
    return doc_text.rstrip() + "\n\n" + block + "\n"


def render_report(results: list[dict], *, forward_min_days: float, now_iso: str) -> str:
    """The detailed, regenerated-wholesale comparison report (data/reports/)."""
    ranked = sorted(results, key=_rank_key)
    out = [
        "# Strategy validation campaign — comparison report",
        "",
        f"_Generated by `make campaign` at **{now_iso}**. Regenerated wholesale each run._",
        "",
        "**Risk-first ranking.** There is no proven long-only TA edge on the 8-token universe "
        "(docs/bnb_strategy_decision.md §1), so arms are ranked by survival + lowest worst-week "
        "drawdown, **not** by return. The 25% worst-week DD ceiling is the hard pass/fail rail "
        "(inside the 30% contest DQ line; 15% is the stretch target).",
        "",
        f"Backtest friction: **0.70% one-way (binding spot-DEX RT)**. Forward window: "
        f"**{forward_min_days:g}d** (contest-compressed; rigorous default 14d).",
        "",
        "| # | Arm | Alias | Stage | Survival | wkDD | t/wk | meanWk | Forward | fwd 7dDD | fwd t/wk | fwd medWk |",
        "|--:|---|---|:--:|:--:|--:|--:|--:|---|--:|--:|--:|",
    ]
    for i, r in enumerate(ranked, 1):
        alias = r.get("alias") or "—"
        if r.get("error"):
            out.append(f"| {i} | `{r['arm']}` | {alias} | — | ⚠️ | — | — | — | {r['error']} | — | — | — |")
            continue
        sv, fw = r["survival"], r.get("forward") or {}
        passed = "✅" if sv["passed"] else "❌"
        meanwk = f"{r['mean_ret']*100:+.2f}%" if r.get("mean_ret") is not None else "—"
        f_dd = f"{fw['worst_7d_dd']*100:.1f}%" if "worst_7d_dd" in fw else "—"
        f_tpw = f"{fw['trades_per_week']:.1f}" if "trades_per_week" in fw else "—"
        f_mwk = (f"{fw['median_weekly_ret']*100:+.2f}%"
                 if fw.get("median_weekly_ret") is not None else "—")
        out.append(
            f"| {i} | `{r['arm']}` | {alias} | {r['stage']} | {passed} | "
            f"{sv['worst_week_dd']*100:.1f}% | {sv['trades_per_week']:.1f} | {meanwk} | "
            f"{_forward_cell(fw)} | {f_dd} | {f_tpw} | {f_mwk} |"
        )
    out += ["", *render_scoreboard(ranked)]
    out += [
        "",
        "## Per-arm summaries",
        "",
    ]
    for r in ranked:
        if r.get("error"):
            out.append(f"- **{r['arm']}** — ⚠️ {r['error']}")
        else:
            out.append(f"- **{r['arm']}** — {r.get('summary', '')}")
    out += [
        "",
        "_Survival verdicts persist to `data/reports/strategy_gates.json` (`survival` key); "
        "forward verdicts under `forward`. The dashboard strategy selector badges each arm from "
        "the same file. No arm is live-eligible without operator sign-off (Part 7)._",
        "",
    ]
    return "\n".join(out)


def run_campaign(*, limit: int = 2500, static_cap: bool = False,
                 forward_min_days: float = DEFAULT_FORWARD_MIN_DAYS, save: bool = True,
                 doc_path: Path = DOC_PATH, report_path: Path = REPORT_PATH,
                 now_iso: str | None = None, journal: list[dict] | None = None,
                 frames: dict | None = None) -> list[dict]:
    """Run the whole campaign and (when `save`) persist verdicts + rewrite the doc/report."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")
    journal = read_journal(limit=5000) if journal is None else journal
    arms = real_arms()
    # Fetch the universe ONCE and reuse across every arm (8 fetches, not 8×N).
    frames = frames if frames is not None else vs.load_frames(limit)
    results = [
        evaluate_arm(a, journal, limit=limit, static_cap=static_cap,
                     forward_min_days=forward_min_days, save=save, now_iso=now_iso, frames=frames)
        for a in arms
    ]

    if save:
        guardian = render_guardian(results, forward_min_days=forward_min_days, now_iso=now_iso)
        if doc_path.exists():
            doc_path.write_text(splice_guardian(doc_path.read_text(encoding="utf-8"), guardian),
                                encoding="utf-8")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            render_report(results, forward_min_days=forward_min_days, now_iso=now_iso),
            encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Wire every registered arm through validate + "
                                             "forward-promote + save, in one shot.")
    ap.add_argument("--limit", type=int, default=2500)
    ap.add_argument("--static-cap", action="store_true",
                    help="use the static deploy_cap instead of the regime cap_series")
    ap.add_argument("--forward-min-days", type=float, default=DEFAULT_FORWARD_MIN_DAYS,
                    help=f"forward span to evaluate (default {DEFAULT_FORWARD_MIN_DAYS:g}d, "
                         "contest-compressed; pass 14 for the rigorous window)")
    ap.add_argument("--no-save", action="store_true",
                    help="dry run: print the table, do NOT persist verdicts or rewrite docs")
    args = ap.parse_args()

    save = not args.no_save
    arms = real_arms()
    print(f"strategy validation campaign — {len(arms)} arms, forward window "
          f"{args.forward_min_days:g}d (contest-compressed), 0.70% binding friction"
          f"{'  [DRY RUN]' if not save else ''}\n")
    results = run_campaign(limit=args.limit, static_cap=args.static_cap,
                           forward_min_days=args.forward_min_days, save=save)

    ranked = sorted(results, key=_rank_key)
    print(f"{'#':>2} {'arm':22} {'stage':>5} {'survival':16} {'t/wk':>6} {'forward':16}")
    print("-" * 74)
    for i, r in enumerate(ranked, 1):
        if r.get("error"):
            print(f"{i:>2} {r['arm']:22} {'—':>5} {'⚠️ ' + r['error']:16}")
            continue
        sv = r["survival"]
        print(f"{i:>2} {r['arm']:22} {r['stage']:>5} {_survival_cell(sv):16} "
              f"{sv['trades_per_week']:6.1f} {_forward_cell(r.get('forward')):16}")
    if save:
        print(f"\nwrote: {DOC_PATH} (guardian matrix)")
        print(f"       {REPORT_PATH} (comparison report)")
        print(f"       {verdicts.VERDICTS_FILE} (survival + forward verdicts)")
        print("\nSIM-only knowledge gathering — the LIVE/contest strategy is unchanged "
              "(operator sign-off required for any live promotion).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
