"""One-shot validation campaign (scripts/strategy_campaign.py): per-arm survival + forward
verdict persistence, the auto-regenerated guardian matrix (markers-only rewrite), risk-first
ranking, idempotency, and the empty-journal degrade. Fully offline — survival_for is stubbed,
the journal/frames/timestamp are injected, and verdict/doc paths point at tmp."""

from __future__ import annotations

from datetime import datetime, timedelta

import scripts.strategy_campaign as sc
from ictbot.engine.acceptance import evaluate_portfolio
from ictbot.runtime import verdicts

NOW = "2026-06-13T00:00:00+00:00"

# Per-arm backtest worst-week DD the stub reports; breakout is forced to FAIL the 25% rail.
DD_MAP = {"momentum_adaptive": 0.10, "dual_momentum": 0.12, "breakout": 0.30}


def _fake_survival(strategy, *, limit=2500, static_cap=False, frames=None):
    dd = DD_MAP.get(strategy, 0.18)
    stats = {
        "worst_week_dd": dd,
        "trades_per_week": 9.0,
        "mean_ret": -0.004,
        "median_ret": -0.003,
        "p5_ret": -0.05,
        "p95_ret": 0.04,
        "pct_up": 0.5,
        "pct_dd_over_30": 0.0,
    }
    return {
        "summary": f"{strategy} — test summary",
        "ok": True,
        "error": None,
        "n_loaded": 8,
        "n_bars": 2000,
        "n_tokens": 8,
        "cols": ["BTC", "ETH"],
        "cap_note": "regime cap",
        "stats_30": stats,
        "stats_70": stats,
        "gate": evaluate_portfolio(stats),
    }


def _journal_with_forward_for(arm: str) -> list[dict]:
    """12 daily rising REBALANCE rows for `arm` (~11d span → eligible at min_days=5)."""
    t0 = datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    return [
        {
            "event": "REBALANCE",
            "strategy": arm,
            "ts": (t0 + timedelta(days=i)).isoformat(),
            "nav_after": 1000 * (1.003**i),
            "n_swaps": 2,
        }
        for i in range(12)
    ]


def _journal_started_not_eligible(arm: str) -> list[dict]:
    """5 rows < MIN_ROWS(10) for `arm`: forward stays 'insufficient' but started=True → Stage 3."""
    t0 = datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    return [
        {
            "event": "REBALANCE",
            "strategy": arm,
            "ts": (t0 + timedelta(days=i)).isoformat(),
            "nav_after": 1000.0,
            "n_swaps": 2,
        }
        for i in range(5)
    ]


def _doc(tmp_path):
    p = tmp_path / "strategy_campaign.md"
    p.write_text(
        "# Title\n\nPROSE BEFORE the guardian.\n\n"
        f"{sc.GUARDIAN_START}\n\n_placeholder_\n\n{sc.GUARDIAN_END}\n\n"
        "PROSE AFTER the guardian.\n",
        encoding="utf-8",
    )
    return p


def _run(tmp_path, monkeypatch, journal):
    monkeypatch.setattr(sc.vs, "survival_for", _fake_survival)
    monkeypatch.setattr(verdicts, "VERDICTS_FILE", tmp_path / "strategy_gates.json")
    doc, report = _doc(tmp_path), tmp_path / "report.md"
    results = sc.run_campaign(
        save=True,
        frames={},
        journal=journal,
        now_iso=NOW,
        doc_path=doc,
        report_path=report,
        forward_min_days=5.0,
    )
    return results, doc, report


def test_real_arms_excludes_aliases():
    arms = sc.real_arms()
    assert "momentum_adaptive" in arms and "dual_momentum" in arms
    assert all(not a.startswith("BNB_STRATEGY_") for a in arms)
    assert len(arms) >= 9


def test_campaign_persists_both_verdicts_for_every_arm(tmp_path, monkeypatch):
    journal = _journal_with_forward_for("dual_momentum")
    results, _, _ = _run(tmp_path, monkeypatch, journal)
    saved = verdicts.load()
    for arm in sc.real_arms():
        assert "survival" in saved[arm] and "forward" in saved[arm]
        # the EXACT survival key set — pins parity with validate_strategy --save-verdict
        # (both writers share vs.survival_payload), so the dashboard badge can't silently drift.
        assert set(saved[arm]["survival"]) == {
            "passed",
            "worst_week_dd",
            "trades_per_week",
            "within_dq_line",
            "target_dd_met",
            "ts",
        }
        assert saved[arm]["survival"]["ts"] == NOW
    # dual_momentum cleared the compressed forward window; an untouched arm did not.
    assert saved["dual_momentum"]["forward"]["status"] == "evaluated"
    assert saved["dual_momentum"]["forward"]["forward_eligible"] is True
    assert saved["rotation"]["forward"]["status"] == "insufficient forward data"
    # breakout fails the 25% survival rail; the locked default passes.
    assert saved["breakout"]["survival"]["passed"] is False
    assert saved["momentum_adaptive"]["survival"]["passed"] is True


def test_stages_and_ranking(tmp_path, monkeypatch):
    # dual_momentum → forward-eligible (Stage 4); rotation → started-but-insufficient (Stage 3).
    journal = _journal_with_forward_for("dual_momentum") + _journal_started_not_eligible("rotation")
    results, _, _ = _run(tmp_path, monkeypatch, journal)
    by_arm = {r["arm"]: r for r in results}
    assert by_arm["momentum_adaptive"]["stage"] == 5  # locked default, operator sign-off
    assert by_arm["dual_momentum"]["stage"] == 4  # survival + started + forward-eligible
    assert by_arm["rotation"]["started"] is True  # has journal rows...
    assert by_arm["rotation"]["forward"]["forward_eligible"] is False  # ...but < MIN_ROWS
    assert by_arm["rotation"]["stage"] == 3  # forward-started, not yet eligible
    assert by_arm["momentum_voltarget"]["stage"] == 2  # survival only, no forward data
    assert by_arm["breakout"]["stage"] == 1  # failed survival
    # Risk-first: the failing arm ranks last.
    order = [r["arm"] for r in sorted(results, key=sc._rank_key)]
    assert order[-1] == "breakout"
    assert order.index("momentum_adaptive") < order.index("dual_momentum")  # 0.10 < 0.12 DD


def test_signed_off_arm_failing_survival_is_not_stage_5(tmp_path, monkeypatch):
    # If the locked default ever fails this run's survival gate, the matrix must NOT label it
    # Stage 5 (which would imply stages 1–4 cleared next to a visible FAIL).
    monkeypatch.setitem(
        DD_MAP, "momentum_adaptive", 0.40
    )  # > 25% rail → survival FAIL (auto-reverts)
    results, _, _ = _run(tmp_path, monkeypatch, [])
    by_arm = {r["arm"]: r for r in results}
    assert by_arm["momentum_adaptive"]["survival"]["passed"] is False
    assert by_arm["momentum_adaptive"]["stage"] == 1  # honest: not Stage 5 while survival fails


def test_guardian_block_rewrite_preserves_prose(tmp_path, monkeypatch):
    journal = _journal_with_forward_for("dual_momentum")
    _, doc, report = _run(tmp_path, monkeypatch, journal)
    text = doc.read_text(encoding="utf-8")
    assert "PROSE BEFORE the guardian." in text and "PROSE AFTER the guardian." in text
    assert "_placeholder_" not in text
    body = text.split(sc.GUARDIAN_START)[1].split(sc.GUARDIAN_END)[0]
    assert "`dual_momentum`" in body and "Forward window **5d**" in body
    # the evaluated+eligible branch must RENDER (not just persist): the guardian cell string
    # and the report's numeric forward cells, derived from the verdict so they can't go stale.
    assert "✅ eligible" in body
    fw = verdicts.load()["dual_momentum"]["forward"]
    rpt = report.read_text(encoding="utf-8")
    assert "comparison report" in rpt
    assert f"{fw['worst_7d_dd'] * 100:.1f}%" in rpt
    assert f"{fw['trades_per_week']:.1f}" in rpt
    assert f"{fw['median_weekly_ret'] * 100:+.2f}%" in rpt


def test_perf_scoreboard_persists_and_renders(tmp_path, monkeypatch):
    # The PnL/win-rate SCOREBOARD is additive to the risk-first gate: a new "perf" verdict kind
    # is persisted alongside survival/forward (never inside the pinned survival payload), and the
    # report grows a clearly no-edge-framed scoreboard section with BOTH win-rate notions.
    journal = _journal_with_forward_for("dual_momentum")
    results, _, report = _run(tmp_path, monkeypatch, journal)
    saved = verdicts.load()
    assert "perf" in saved["dual_momentum"]  # new kind, additive
    assert set(saved["dual_momentum"]["perf"]) == {
        "total_return",
        "win_rate",
        "mean_ret",
        "median_ret",
        "ts",
    }
    assert saved["dual_momentum"]["perf"]["win_rate"] == 0.5  # = pct_up (WINDOW win-rate)
    # survival payload stays exactly pinned (the perf kind didn't leak into it)
    assert set(saved["dual_momentum"]["survival"]) == {
        "passed",
        "worst_week_dd",
        "trades_per_week",
        "within_dq_line",
        "target_dd_met",
        "ts",
    }
    rpt = report.read_text(encoding="utf-8")
    assert "## PnL / win-rate scoreboard" in rpt
    assert "scoreboard, not an edge claim" in rpt
    assert "Win-rate (window)" in rpt and "Win-rate (day)" in rpt
    # the rising journal → forward DAY win-rate fully resolved (every traded day up)
    fperf = next(r for r in results if r["arm"] == "dual_momentum")["fwd_perf"]
    assert fperf["status"] == "evaluated" and fperf["win_rate"] == 1.0
    assert "100% (11/11d)" in rpt


def test_idempotent_modulo_timestamp(tmp_path, monkeypatch):
    journal = _journal_with_forward_for("dual_momentum")
    _, doc, _ = _run(tmp_path, monkeypatch, journal)
    first = doc.read_text(encoding="utf-8")
    _run(tmp_path, monkeypatch, journal)  # same inputs + same NOW
    assert doc.read_text(encoding="utf-8") == first


def test_empty_journal_all_forward_insufficient(tmp_path, monkeypatch):
    results, _, _ = _run(tmp_path, monkeypatch, [])
    for r in results:
        assert r["forward"]["status"] == "insufficient forward data"
        assert r["forward"]["forward_eligible"] is False


def test_no_save_does_not_persist_or_rewrite(tmp_path, monkeypatch):
    monkeypatch.setattr(sc.vs, "survival_for", _fake_survival)
    monkeypatch.setattr(verdicts, "VERDICTS_FILE", tmp_path / "strategy_gates.json")
    doc, report = _doc(tmp_path), tmp_path / "report.md"
    before = doc.read_text(encoding="utf-8")
    sc.run_campaign(
        save=False,
        frames={},
        journal=[],
        now_iso=NOW,
        doc_path=doc,
        report_path=report,
        forward_min_days=5.0,
    )
    assert verdicts.load() == {}  # nothing persisted
    assert doc.read_text(encoding="utf-8") == before  # doc untouched
    assert not report.exists()  # report not written


def test_error_arms_render_and_persist_nothing(tmp_path, monkeypatch):
    # The most likely production path: an arm whose data fetch fails (ok=False) or that is
    # missing from the registry (KeyError). Must not crash, must render ⚠️, persist nothing.
    def fake(strategy, *, limit=2500, static_cap=False, frames=None):
        if strategy == "breakout":
            raise KeyError(f"unknown strategy {strategy!r}")
        if strategy == "rotation":
            return {
                "summary": "rotation x",
                "ok": False,
                "error": "not enough aligned data",
                "n_loaded": 1,
                "n_bars": 100,
                "n_tokens": 2,
                "cols": ["BTC"],
            }
        return _fake_survival(strategy)

    monkeypatch.setattr(sc.vs, "survival_for", fake)
    monkeypatch.setattr(verdicts, "VERDICTS_FILE", tmp_path / "strategy_gates.json")
    doc, report = _doc(tmp_path), tmp_path / "report.md"
    results = sc.run_campaign(
        save=True,
        frames={},
        journal=[],
        now_iso=NOW,
        doc_path=doc,
        report_path=report,
        forward_min_days=5.0,
    )
    by_arm = {r["arm"]: r for r in results}
    assert "error" in by_arm["breakout"] and "survival" not in by_arm["breakout"]  # KeyError path
    assert "error" in by_arm["rotation"] and "survival" not in by_arm["rotation"]  # ok=False path
    saved = verdicts.load()
    assert "breakout" not in saved and "rotation" not in saved  # error arms persist nothing
    body = doc.read_text(encoding="utf-8").split(sc.GUARDIAN_START)[1].split(sc.GUARDIAN_END)[0]
    assert "⚠️" in body and "⚠️" in report.read_text(encoding="utf-8")  # error rows render
    order = [r["arm"] for r in sorted(results, key=sc._rank_key)]
    assert set(order[-2:]) == {"breakout", "rotation"}  # errored arms rank last


def test_splice_appends_when_no_markers():
    block = sc.render_guardian([], forward_min_days=5.0, now_iso=NOW)
    out = sc.splice_guardian("# Doc\n\nbody text", block)
    assert out.count(sc.GUARDIAN_START) == 1 and out.count(sc.GUARDIAN_END) == 1
    assert "body text" in out and out.rstrip().endswith(sc.GUARDIAN_END)


def test_splice_inserts_block_literally_and_first_pair_only():
    # regex-special content in the block survives verbatim (lambda replacement, not a
    # backref string), and only the FIRST marker pair is rewritten (count=1).
    block = f"{sc.GUARDIAN_START}\nliteral \\1 and \\g<0> and $5.00\n{sc.GUARDIAN_END}"
    doc = (
        f"A\n{sc.GUARDIAN_START}\nOLD1\n{sc.GUARDIAN_END}\nMID\n"
        f"{sc.GUARDIAN_START}\nOLD2\n{sc.GUARDIAN_END}\nZ"
    )
    out = sc.splice_guardian(doc, block)
    assert "literal \\1 and \\g<0> and $5.00" in out
    assert "OLD1" not in out and "OLD2" in out
