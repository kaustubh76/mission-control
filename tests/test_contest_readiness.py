"""contest_readiness rollup: the readiness verdict across all four states, the isolated-track
preference over the campaign forward verdict, SIM-only (persists nothing), and report rendering.
Offline — loaders injected, DATA_DIR redirected to tmp."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import scripts.contest_readiness as cr
from ictbot.runtime import verdicts


def test_readiness_states():
    assert (
        cr._readiness(
            "breakout",
            {"grade": "ROBUST"},
            {"passed": True},
            {"status": "evaluated", "forward_eligible": True},
        )[0]
        == cr.READY
    )
    v, b = cr._readiness(
        "breakout",
        {"grade": "ROBUST"},
        {"passed": True},
        {"status": "insufficient forward data", "forward_eligible": False},
    )
    assert v == cr.IN_PROGRESS and "accruing" in b
    v, b = cr._readiness(
        "breakout",
        {"grade": "FRAGILE"},
        {"passed": True},
        {"status": "evaluated", "forward_eligible": False},
    )
    assert v == cr.IN_PROGRESS and "not yet" in b
    assert (
        cr._readiness("breakout", {"grade": "ROBUST"}, {"passed": False}, None)[0] == cr.NOT_READY
    )
    v, b = cr._readiness(
        "breakout", {"grade": "UNSTABLE"}, {"passed": True}, {"forward_eligible": True}
    )
    assert v == cr.NOT_READY and "UNSTABLE" in b
    assert (
        cr._readiness("momentum_adaptive", {"grade": "FRAGILE"}, {"passed": True}, None)[0]
        == cr.INCUMBENT_TAG
    )


def test_run_readiness_uses_campaign_forward_and_ranks(monkeypatch, tmp_path):
    monkeypatch.setattr(cr, "DATA_DIR", tmp_path)  # no isolated tracks → campaign forward
    vmap = {
        "dual_momentum": {
            "survival": {"passed": True, "worst_week_dd": 0.11},
            "forward": {"status": "evaluated", "forward_eligible": True},
        },
        "breakout": {
            "survival": {"passed": True, "worst_week_dd": 0.14},
            "forward": {"status": "insufficient forward data", "forward_eligible": False},
        },
    }
    gmap = {
        "dual_momentum": {"grade": "ROBUST"},
        "breakout": {"grade": "ROBUST"},
        "momentum_adaptive": {"grade": "FRAGILE"},
    }
    res = cr.run_readiness(save=False, vmap=vmap, gmap=gmap, now_iso="t")
    by = {r["arm"]: r for r in res}
    assert by["dual_momentum"]["verdict"] == cr.READY
    assert by["breakout"]["verdict"] == cr.IN_PROGRESS
    assert by["momentum_adaptive"]["verdict"] == cr.INCUMBENT_TAG
    order = [r["arm"] for r in res]
    assert order.index("dual_momentum") < order.index("breakout")  # READY before IN PROGRESS


def test_isolated_track_preferred_over_campaign(monkeypatch, tmp_path):
    monkeypatch.setattr(cr, "DATA_DIR", tmp_path)
    jdir = tmp_path / "forward" / "dual_momentum" / "journal"
    jdir.mkdir(parents=True)
    t0 = datetime(2026, 5, 1)
    lines = [
        json.dumps(
            {
                "event": "REBALANCE",
                "strategy": "dual_momentum",
                "ts": (t0 + timedelta(days=i)).isoformat(),
                "nav_after": 1000 * (1.01**i),
                "n_swaps": 2,
            }
        )
        for i in range(12)
    ]
    (jdir / "allocator_journal.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # campaign says insufficient, but the isolated rising track is eligible → isolated wins
    vmap = {
        "dual_momentum": {
            "survival": {"passed": True, "worst_week_dd": 0.11},
            "forward": {"status": "insufficient forward data", "forward_eligible": False},
        }
    }
    gmap = {"dual_momentum": {"grade": "ROBUST"}}
    r = next(
        x
        for x in cr.run_readiness(save=False, vmap=vmap, gmap=gmap, forward_min_days=5, now_iso="t")
        if x["arm"] == "dual_momentum"
    )
    assert r["forward_src"] == "isolated"
    assert r["forward"]["forward_eligible"] is True
    assert r["verdict"] == cr.READY


def test_readiness_persists_no_verdicts(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise AssertionError("readiness must never persist verdicts")

    monkeypatch.setattr(verdicts, "record", _boom)
    monkeypatch.setattr(cr, "DATA_DIR", tmp_path)
    cr.run_readiness(save=True, report_path=tmp_path / "r.md", vmap={}, gmap={}, now_iso="t")
    assert (tmp_path / "r.md").exists()  # only the report is written


def test_report_renders():
    rpt = cr.render_report(
        [
            {
                "arm": "breakout",
                "verdict": cr.READY,
                "stability": {"grade": "ROBUST"},
                "survival": {"passed": True, "worst_week_dd": 0.14},
                "forward": {"status": "evaluated", "forward_eligible": True},
                "forward_src": "isolated",
                "blocker": "all gates cleared",
            }
        ],
        forward_min_days=5,
        now_iso="t",
    )
    assert "Contest-readiness rollup" in rpt and "`breakout`" in rpt
    assert "**Recommendation:**" in rpt


# --- deploy_summary: cash-vacuous vs deployed ------------------------------------------------- #
def _rows(arm, *, navs, swaps, caps):
    return [
        {
            "event": "REBALANCE",
            "strategy": arm,
            "ts": f"2026-05-0{i + 1}T00:00:00",
            "nav_after": navs[i],
            "n_swaps": swaps[i],
            "deploy_cap": caps[i],
        }
        for i in range(len(navs))
    ]


def test_deploy_summary_cash_vacuous():
    rows = _rows("dual_momentum", navs=[1000.0] * 4, swaps=[0, 0, 0, 0], caps=[0.0, 0.0, 0.0, 0.0])
    d = cr.deploy_summary(rows, "dual_momentum")
    assert d["deployed"] is False and d["deployed_fraction"] == 0.0 and d["mean_deploy_cap"] == 0.0


def test_deploy_summary_deployed():
    rows = _rows(
        "breakout", navs=[1000, 999, 998, 999], swaps=[8, 0, 4, 0], caps=[0.66, 0.66, 0.5, 0.5]
    )
    d = cr.deploy_summary(rows, "breakout")
    assert d["deployed"] is True and 0.0 < d["deployed_fraction"] <= 1.0


def test_deploy_summary_tolerates_missing_deploy_cap():
    rows = [
        {
            "event": "REBALANCE",
            "strategy": "x",
            "ts": "2026-05-01T00:00:00",
            "nav_after": 1000.0,
            "n_swaps": 3,
        }
    ]  # older schema: no deploy_cap
    d = cr.deploy_summary(rows, "x")
    assert (
        d["mean_deploy_cap"] is None and d["deployed"] is True
    )  # n_swaps>0 still counts as deployed


def test_forward_cell_labels_cash_vs_young():
    accruing = {"status": "insufficient forward data"}
    cash = cr._forward_cell(accruing, "isolated", {"n_rows": 4, "deployed": False})
    young = cr._forward_cell(accruing, "isolated", {"n_rows": 4, "deployed": True})
    assert "cash" in cash and "deploy_cap≈0" in cash
    assert "4 rows" in young and "cash" not in young


# --- recommend_arm ---------------------------------------------------------------------------- #
def _ready(arm, dd, grade="ROBUST", deployed=True):
    return {
        "arm": arm,
        "verdict": cr.READY,
        "stability": {"grade": grade},
        "survival": {"passed": True, "worst_week_dd": dd},
        "forward": {"status": "evaluated", "forward_eligible": True},
        "deploy": {"deployed": deployed, "n_rows": 12},
    }


def test_recommend_promotes_lowest_dd_ready_robust_deployed():
    res = [_ready("dual_momentum", 0.11), _ready("rotation", 0.16)]  # already risk-first ordered
    rec = cr.recommend_arm(res)
    assert rec["action"] == "PROMOTE-CANDIDATE" and rec["arm"] == "dual_momentum"
    assert "PROMOTE-CANDIDATE: dual_momentum" in rec["line"]


def test_recommend_stay_when_none_ready():
    res = [
        {
            "arm": "breakout",
            "verdict": cr.IN_PROGRESS,
            "stability": {"grade": "ROBUST"},
            "survival": {"passed": True, "worst_week_dd": 0.13},
            "deploy": {"deployed": True},
        }
    ]
    assert (
        cr.recommend_arm(res)["action"] == "STAY"
        and "STAY INCUMBENT" in cr.recommend_arm(res)["line"]
    )


def test_recommend_rejects_fragile_ready():
    assert cr.recommend_arm([_ready("mean_reversion", 0.15, grade="FRAGILE")])["action"] == "STAY"


def test_recommend_rejects_cash_vacuous_track():
    # READY + ROBUST but the forward track never deployed (cash) → not real evidence → STAY.
    assert cr.recommend_arm([_ready("dual_momentum", 0.11, deployed=False)])["action"] == "STAY"


def test_recommend_excludes_incumbent():
    inc = {
        "arm": cr.INCUMBENT,
        "verdict": cr.READY,
        "stability": {"grade": "ROBUST"},
        "survival": {"passed": True, "worst_week_dd": 0.10},
        "deploy": {"deployed": True},
    }
    assert cr.recommend_arm([inc])["action"] == "STAY"


def test_recommend_is_pure_no_persist(monkeypatch):
    monkeypatch.setattr(
        verdicts, "record", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no persist"))
    )
    cr.recommend_arm([_ready("dual_momentum", 0.11)])  # must not touch the ledger
