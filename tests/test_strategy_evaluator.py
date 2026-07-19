"""
Forward-gated strategy auto-selector — scoring, DQ-safe gating, anti-chasing hysteresis, and the
recommend-only-for-live boundary. We stub `run_readiness`/`recommend_arm` (their internals are tested
in test_contest_readiness/forward) and drive the evaluator's OWN logic deterministically.
"""

from __future__ import annotations

import json

import scripts.contest_readiness as cr
from ictbot.runtime import strategy_evaluator as se


def _result(arm, *, passed=True, worst_dd=0.10, grade="ROBUST",
            fwd_elig=False, mwr=None, fwd_dd=None):
    return {
        "arm": arm,
        "stability": {"grade": grade},
        "survival": {"passed": passed, "worst_week_dd": worst_dd},
        "forward": ({"forward_eligible": True, "median_weekly_ret": mwr, "worst_7d_dd": fwd_dd}
                    if fwd_elig else {"forward_eligible": False, "status": "accruing"}),
        "deploy": {"deployed": True},
        "verdict": "✅ READY (sign-off)" if fwd_elig else "⏳ IN PROGRESS",
        "blocker": "",
    }


def _wire(monkeypatch, *, results, champion, incumbent="momentum_adaptive",
          margin=0.02, sustain=2, sim=True, live=False):
    monkeypatch.setattr(cr, "run_readiness", lambda **k: results)
    monkeypatch.setattr(cr, "recommend_arm",
                        lambda res, **k: ({"action": "PROMOTE-CANDIDATE", "arm": champion}
                                          if champion else {"action": "STAY", "arm": None, "line": "STAY"}))
    monkeypatch.setattr(se, "_live_arm", lambda: incumbent)
    monkeypatch.setattr(se.settings, "strategy_select_margin", margin)
    monkeypatch.setattr(se.settings, "strategy_select_sustain", sustain)
    monkeypatch.setattr(se.settings, "strategy_auto_select_sim", sim)
    monkeypatch.setattr(se.settings, "strategy_auto_apply_live", live)


# ------------------------------- scoring + ranking ------------------------------- #
def test_score_forward_eligible_uses_forward_metrics(monkeypatch):
    r = _result("alpha", fwd_elig=True, mwr=0.05, fwd_dd=0.10)
    assert se._score(r, {}) == 0.05 - 0.10  # median_weekly_ret − worst_7d_dd


def test_score_fallback_uses_perf_minus_survival_dd():
    r = _result("beta", worst_dd=0.12)
    vmap = {"beta": {"perf": {"total_return": -0.40}}}
    assert se._score(r, vmap) == -0.40 - 0.12


def test_ranking_orders_by_score_desc(monkeypatch):
    results = [_result("beta", worst_dd=0.12), _result("alpha", fwd_elig=True, mwr=0.05, fwd_dd=0.10)]
    _wire(monkeypatch, results=results, champion=None, incumbent="beta")
    d = se.evaluate(vmap={"beta": {"perf": {"total_return": -0.40}}}, gmap={}, state={})
    arms = [s["arm"] for s in d["scores"]]
    assert arms[0] == "alpha"  # -0.05 beats beta's -0.52


# ------------------------------- DQ-safe gating ------------------------------- #
def test_gating_excludes_survival_fail_and_unstable(monkeypatch):
    results = [
        _result("good"),
        _result("failsurv", passed=False),
        _result("unstable", grade="UNSTABLE"),
    ]
    _wire(monkeypatch, results=results, champion=None)
    d = se.evaluate(vmap={}, gmap={}, state={})
    flags = {s["arm"]: s["dq_safe"] for s in d["scores"]}
    assert flags["good"] is True and flags["failsurv"] is False and flags["unstable"] is False


# ------------------------------- hysteresis ------------------------------- #
def test_no_switch_when_sustain_not_met(monkeypatch):
    # champion clearly beats incumbent, but it's the FIRST eval (consecutive→1 < sustain 2) → STAY.
    results = [_result("alpha", fwd_elig=True, mwr=0.05, fwd_dd=0.10),
               _result("momentum_adaptive", worst_dd=0.12)]
    _wire(monkeypatch, results=results, champion="alpha", sustain=2)
    d = se.evaluate(vmap={"momentum_adaptive": {"perf": {"total_return": -0.50}}}, gmap={},
                    state={"last_champion": None, "consecutive": 0})
    assert d["action"] == "STAY" and d["switch"] is False and d["hysteresis"]["consecutive"] == 1


def test_switch_when_sustained_and_margin_met(monkeypatch):
    results = [_result("alpha", fwd_elig=True, mwr=0.05, fwd_dd=0.10),
               _result("momentum_adaptive", worst_dd=0.12)]
    _wire(monkeypatch, results=results, champion="alpha", sustain=2)
    # prior state already had alpha as champion once → this eval makes consecutive=2 == sustain.
    d = se.evaluate(vmap={"momentum_adaptive": {"perf": {"total_return": -0.50}}}, gmap={},
                    state={"last_champion": "alpha", "consecutive": 1})
    assert d["action"] == "SWITCH" and d["switch"] is True and d["recommended"] == "alpha"


def test_no_switch_when_margin_not_met(monkeypatch):
    # champion sustained but only 0.01 better than incumbent (< margin 0.02) → STAY.
    results = [_result("alpha", fwd_elig=True, mwr=0.05, fwd_dd=0.10),       # score -0.05
               _result("momentum_adaptive", worst_dd=0.10)]                  # fallback score -0.06
    _wire(monkeypatch, results=results, champion="alpha", sustain=1, margin=0.02)
    d = se.evaluate(vmap={"momentum_adaptive": {"perf": {"total_return": 0.04}}}, gmap={},
                    state={"last_champion": "alpha", "consecutive": 1})
    assert d["action"] == "STAY" and d["switch"] is False


# ------------------------------- apply boundaries ------------------------------- #
def test_apply_sim_writes_only_on_switch_when_enabled(monkeypatch):
    saved = []
    import ictbot.runtime.strategy_select as ss
    monkeypatch.setattr(ss, "save", lambda name: saved.append(name) or name)
    monkeypatch.setattr(se.settings, "strategy_auto_select_sim", True)
    assert se.apply_sim({"switch": True, "recommended": "alpha"}) == "alpha" and saved == ["alpha"]
    saved.clear()
    assert se.apply_sim({"switch": False, "recommended": "alpha"}) is None and saved == []


def test_apply_sim_noop_when_disabled(monkeypatch):
    saved = []
    import ictbot.runtime.strategy_select as ss
    monkeypatch.setattr(ss, "save", lambda name: saved.append(name))
    monkeypatch.setattr(se.settings, "strategy_auto_select_sim", False)
    assert se.apply_sim({"switch": True, "recommended": "alpha"}) is None and saved == []


def test_apply_live_recommend_only_by_default(monkeypatch):
    calls = []
    import ictbot.runtime.kill_switch as ks
    monkeypatch.setattr(ks, "rewrite_env_key", lambda k, v: calls.append((k, v)))
    monkeypatch.setattr(se.settings, "strategy_auto_apply_live", False)
    assert se.apply_live({"switch": True, "recommended": "alpha"}) is None and calls == []
    # opt-in flips it on
    monkeypatch.setattr(se.settings, "strategy_auto_apply_live", True)
    assert se.apply_live({"switch": True, "recommended": "alpha"}) == "alpha"
    assert calls == [("STRATEGY_NAME", "alpha")]


# ------------------------------- forward-min-days gate ------------------------------- #
def test_forward_min_days_passed_through(monkeypatch):
    """The configured forward window must reach `run_readiness` (and an explicit override wins) — it's
    the gate that keeps young, under-accrued tracks from being judged forward-eligible."""
    captured = {}

    def _rr(**k):
        captured.update(k)
        return []

    monkeypatch.setattr(cr, "run_readiness", _rr)
    monkeypatch.setattr(cr, "recommend_arm", lambda res, **k: {"action": "STAY", "arm": None, "line": "x"})
    monkeypatch.setattr(se, "_live_arm", lambda: "momentum_adaptive")

    se.evaluate(vmap={}, gmap={}, state={})
    assert captured["forward_min_days"] == se.settings.strategy_select_forward_min_days
    assert captured["save"] is False  # never persists the readiness report from the selector
    se.evaluate(forward_min_days=12.5, vmap={}, gmap={}, state={})
    assert captured["forward_min_days"] == 12.5


# ------------------------------- end-to-end run() over REAL state-file I/O ------------------------------- #
# These drive the full `run()` pass against isolated on-disk artifacts (not an injected state dict), so they
# prove the hysteresis counter, recommendation, and SIM selector actually persist across cron invocations.
# Champion/incumbent are REAL registered arms so `strategy_select.save` (registry-validated) accepts them.

def _isolate_files(tmp_path, monkeypatch):
    """Redirect the three artifacts the selector writes into a per-test tmp dir."""
    import ictbot.runtime.strategy_select as ss
    monkeypatch.setattr(se, "STATE_FILE", tmp_path / "strategy_evaluator_state.json")
    monkeypatch.setattr(se, "RECO_FILE", tmp_path / "strategy_evaluation.json")
    monkeypatch.setattr(ss, "STRATEGY_SELECT_FILE", tmp_path / "strategy_select.json")
    return ss


def _live_results():
    """A forward-eligible challenger (high score) vs a weaker DQ-safe incumbent (score via −worst_dd)."""
    return [_result("dual_momentum", fwd_elig=True, mwr=0.05, fwd_dd=0.0),
            _result("mean_reversion", worst_dd=0.15)]


def test_sustain_persists_across_real_runs(tmp_path, monkeypatch):
    ss = _isolate_files(tmp_path, monkeypatch)
    _wire(monkeypatch, results=_live_results(), champion="dual_momentum",
          incumbent="mean_reversion", sustain=2)
    # Run 1: challenger qualifies but it's the first eval → STAY, consecutive persisted as 1, no SIM write.
    d1 = se.run()
    assert d1["action"] == "STAY" and d1["hysteresis"]["consecutive"] == 1
    assert json.loads(se.STATE_FILE.read_text())["consecutive"] == 1
    assert not ss.STRATEGY_SELECT_FILE.exists()
    # Run 2: counter LOADED from disk increments to 2 == sustain → SWITCH (proves cross-run persistence).
    d2 = se.run()
    assert d2["action"] == "SWITCH" and d2["hysteresis"]["consecutive"] == 2
    assert json.loads(se.STATE_FILE.read_text())["consecutive"] == 2


def test_run_writes_all_artifacts_on_switch(tmp_path, monkeypatch):
    ss = _isolate_files(tmp_path, monkeypatch)
    _wire(monkeypatch, results=_live_results(), champion="dual_momentum",
          incumbent="mean_reversion", sustain=1)  # sustain=1 → switches on the first eval
    d = se.run()
    assert d["action"] == "SWITCH" and d["sim_applied"] == "dual_momentum" and d["live_applied"] is None
    # SIM selector
    assert json.loads(ss.STRATEGY_SELECT_FILE.read_text())["strategy"] == "dual_momentum"
    # live recommendation surface (dashboard + operator)
    reco = json.loads(se.RECO_FILE.read_text())
    assert reco["switch"] is True and reco["recommended_live_arm"] == "dual_momentum"
    assert reco["apply_cmd"].endswith("dual_momentum") and reco["auto_apply_live"] is False
    # hysteresis memory
    state = json.loads(se.STATE_FILE.read_text())
    assert state["last_champion"] == "dual_momentum" and state["consecutive"] == 1
    assert len(state["history"]) == 1 and state["history"][0]["action"] == "SWITCH"


def test_apply_live_via_run_when_opted_in(tmp_path, monkeypatch):
    _isolate_files(tmp_path, monkeypatch)
    calls = []
    import ictbot.runtime.kill_switch as ks
    monkeypatch.setattr(ks, "rewrite_env_key", lambda k, v: calls.append((k, v)))
    # default off → run() switches the SIM book but NEVER the live env
    _wire(monkeypatch, results=_live_results(), champion="dual_momentum",
          incumbent="mean_reversion", sustain=1, live=False)
    d_off = se.run()
    assert d_off["action"] == "SWITCH" and d_off["live_applied"] is None and calls == []
    # opt-in on → run() rewrites STRATEGY_NAME exactly once
    _wire(monkeypatch, results=_live_results(), champion="dual_momentum",
          incumbent="mean_reversion", sustain=1, live=True)
    d_on = se.run()
    assert d_on["live_applied"] == "dual_momentum" and calls == [("STRATEGY_NAME", "dual_momentum")]


def test_champion_change_resets_sustain(tmp_path, monkeypatch):
    """A DIFFERENT champion each eval must RESET the consecutive counter — anti-chasing across a churn
    of one-off leaders, not just a single sustained one."""
    _isolate_files(tmp_path, monkeypatch)
    _wire(monkeypatch, results=_live_results(), champion="dual_momentum",
          incumbent="mean_reversion", sustain=2)
    d1 = se.run()
    assert d1["champion"] == "dual_momentum" and d1["hysteresis"]["consecutive"] == 1 and d1["action"] == "STAY"
    # the champion changes → counter resets to 1 (it must NOT carry over toward sustain)
    res2 = [_result("grid", fwd_elig=True, mwr=0.06, fwd_dd=0.0), _result("mean_reversion", worst_dd=0.15)]
    _wire(monkeypatch, results=res2, champion="grid", incumbent="mean_reversion", sustain=2)
    d2 = se.run()
    assert d2["champion"] == "grid" and d2["hysteresis"]["consecutive"] == 1 and d2["action"] == "STAY"
