"""sim_test_all harness (scripts/sim_test_all.py): the pure validate_arm — a valid deployed arm, a
valid cash-only (risk-off) arm, malformed rows flagged ERROR, no-rows EMPTY, state ledger-drift
caught — plus the report render and a real over-all-arms read against a synthetic isolated tree.
Fully offline — synthetic journal rows + tmp data dir, no network, no live tick."""

from __future__ import annotations

import json

import scripts.sim_test_all as st


def _reb(strategy="dual_momentum", *, nav=1000.0, target=None, n_swaps=2, **over):
    row = {
        "ts": "2026-06-14T00:00:00+00:00",
        "event": "REBALANCE",
        "mode": "sim",
        "strategy": strategy,
        "nav_before": 1000.0,
        "nav_after": nav,
        "deploy_cap": 0.6,
        "target": {"BNB": 0.3, "CAKE": 0.3} if target is None else target,
        "weights_after": {"BNB": 0.3, "CAKE": 0.3} if target is None else target,
        "n_swaps": n_swaps,
        "tx": ["sim-1"] if n_swaps else [],
    }
    row.update(over)
    return row


def test_valid_deployed_arm_is_ok_with_breadth():
    r = st.validate_arm(
        [_reb(target={"BNB": 0.3, "ETH": 0.2, "CAKE": 0.1})],
        {"cumulative_swaps": 2, "balances": {"BNB": 1.0}},
        "dual_momentum",
    )
    assert r["status"] == "OK" and not r["errors"]
    assert r["distinct_tokens"] == ["BNB", "CAKE", "ETH"] and r["n_distinct"] == 3


def test_cash_only_arm_is_valid():
    # deploy_cap≈0 risk-off: empty target, 0 swaps — a VALID record of the regime, not an error.
    r = st.validate_arm(
        [_reb(target={}, n_swaps=0, deploy_cap=0.0)], {"cumulative_swaps": 0}, "dual_momentum"
    )
    assert r["status"] == "OK" and r["n_distinct"] == 0 and r["total_swaps"] == 0


def test_malformed_rows_flagged_error():
    bad_nav = st.validate_arm([_reb(nav=-5.0)], None, "dual_momentum")
    assert bad_nav["status"] == "ERROR" and any("nav_after" in e for e in bad_nav["errors"])
    over = st.validate_arm([_reb(weights_after={"BNB": 0.7, "CAKE": 0.7})], None, "dual_momentum")
    assert over["status"] == "ERROR" and any("over-deployed" in e for e in over["errors"])
    no_tx = st.validate_arm([_reb(n_swaps=4, tx=[])], None, "dual_momentum")
    assert no_tx["status"] == "ERROR" and any("empty tx" in e for e in no_tx["errors"])
    off_uni = st.validate_arm([_reb(target={"BTC": 0.5})], None, "dual_momentum")
    assert off_uni["status"] == "ERROR" and any("non-universe" in e for e in off_uni["errors"])


def test_no_rows_is_empty():
    assert st.validate_arm([], None, "grid")["status"] == "EMPTY"


def test_state_ledger_drift_caught():
    # journal banked 5 swaps but state forgot them -> read/write inconsistency.
    r = st.validate_arm([_reb(cumulative_swaps=5)], {"cumulative_swaps": 2}, "dual_momentum")
    assert r["status"] == "ERROR" and any("ledger drift" in e for e in r["errors"])


def test_only_matching_strategy_rows_count():
    rows = [_reb(strategy="grid"), _reb(strategy="rotation"), {"event": "DD_HALT"}]
    assert st.validate_arm(rows, None, "grid")["n_rebalances"] == 1


def test_render_report_lists_arms_and_errors():
    res = [
        {
            "arm": "breakout",
            "status": "OK",
            "n_rebalances": 3,
            "total_swaps": 8,
            "distinct_tokens": ["BNB", "ETH"],
            "n_distinct": 2,
            "nav_first": 1000.0,
            "nav_last": 999.8,
            "errors": [],
        },
        {
            "arm": "grid",
            "status": "ERROR",
            "n_rebalances": 1,
            "total_swaps": 0,
            "distinct_tokens": [],
            "n_distinct": 0,
            "nav_first": None,
            "nav_last": None,
            "errors": ["row 0: bad nav_after None"],
        },
    ]
    rpt = st.render_report(res, now_iso="t")
    assert "Sim-test all strategies" in rpt and "`breakout`" in rpt and "✅ OK" in rpt
    assert "## Errors" in rpt and "bad nav_after" in rpt


def test_run_over_arms_reads_isolated_tree(tmp_path):
    # write a synthetic isolated journal for one arm, validate it through the full read path.
    jdir = tmp_path / "forward" / "grid" / "journal"
    jdir.mkdir(parents=True)
    (jdir / "allocator_journal.jsonl").write_text(
        json.dumps(_reb(strategy="grid", target={"BNB": 0.2, "DOGE": 0.2})) + "\n", encoding="utf-8"
    )
    (jdir / "allocator_state.json").write_text(
        json.dumps({"cumulative_swaps": 2, "balances": {}}), encoding="utf-8"
    )
    res = st.run_sim_test_all(arms=["grid"], save=False, now_iso="t", data_dir=tmp_path)
    assert (
        len(res) == 1 and res[0]["status"] == "OK" and res[0]["distinct_tokens"] == ["BNB", "DOGE"]
    )
