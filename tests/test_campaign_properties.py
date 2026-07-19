"""Property-based invariants for the campaign internals (scripts/strategy_campaign.py):
_stage honesty + monotonicity, _rank_key ordering, splice_guardian idempotency + single
marker pair, survival_payload key-set. Hypothesis, repo convention (deadline=None)."""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

import scripts.strategy_campaign as sc
import scripts.validate_strategy as vs
from ictbot.engine.acceptance import evaluate_portfolio

B = st.booleans()


@given(survival_passed=B, started=B, forward_eligible=B, signed_off=B)
@settings(deadline=None)
def test_stage_invariants(survival_passed, started, forward_eligible, signed_off):
    s = sc._stage(
        survival_passed=survival_passed,
        started=started,
        forward_eligible=forward_eligible,
        signed_off=signed_off,
    )
    assert s in (1, 2, 3, 4, 5)
    assert (s == 5) == (signed_off and survival_passed)  # Stage 5 ⟺ signed off AND survives
    if s >= 2:
        assert survival_passed
    if s in (3, 4):
        assert started and survival_passed
    if s == 4:
        assert forward_eligible
    if signed_off and not survival_passed:  # honesty: never 5 next to a FAIL
        assert s != 5


def _surv(arm, passed, dd):
    return {"arm": arm, "survival": {"passed": passed, "worst_week_dd": dd}}


@given(
    survivors=st.lists(st.tuples(B, st.floats(0.0, 0.5)), min_size=1, max_size=8),
    n_errors=st.integers(0, 4),
)
@settings(deadline=None)
def test_rank_key_orders_errors_last_passers_first(survivors, n_errors):
    results = [_surv(f"s{i}", p, dd) for i, (p, dd) in enumerate(survivors)]
    results += [{"arm": f"e{i}", "error": "boom"} for i in range(n_errors)]
    ordered = sorted(results, key=sc._rank_key)
    err = [i for i, r in enumerate(ordered) if r.get("error")]
    surv = [i for i, r in enumerate(ordered) if r.get("survival")]
    assert not surv or not err or min(err) > max(surv)  # errored arms rank after all survivors
    passers = [i for i, r in enumerate(ordered) if r.get("survival") and r["survival"]["passed"]]
    failers = [
        i for i, r in enumerate(ordered) if r.get("survival") and not r["survival"]["passed"]
    ]
    assert not passers or not failers or max(passers) < min(failers)  # PASS before FAIL


@given(text=st.text(max_size=200))
@settings(deadline=None)
def test_splice_idempotent_single_block(text):
    assume(sc.GUARDIAN_START not in text and sc.GUARDIAN_END not in text)
    block = sc.render_guardian([], forward_min_days=5.0, now_iso="2026-06-13T00:00:00+00:00")
    once = sc.splice_guardian(text, block)
    assert once.count(sc.GUARDIAN_START) == 1 and once.count(sc.GUARDIAN_END) == 1
    assert sc.splice_guardian(once, block) == once  # idempotent on re-splice


@given(dd=st.floats(0.0, 1.0), tpw=st.floats(0.0, 40.0))
@settings(deadline=None)
def test_survival_payload_keyset(dd, tpw):
    gate = evaluate_portfolio({"worst_week_dd": dd, "trades_per_week": tpw})
    payload = vs.survival_payload(gate, {"worst_week_dd": dd, "trades_per_week": tpw}, "TS")
    assert set(payload) == {
        "passed",
        "worst_week_dd",
        "trades_per_week",
        "within_dq_line",
        "target_dd_met",
        "ts",
    }
    assert payload["ts"] == "TS" and payload["passed"] == gate.passed
