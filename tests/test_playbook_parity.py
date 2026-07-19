"""Playbook ↔ implementation parity (scripts/playbook_status.py): every Top-10 family maps to a
REGISTERED arm and every registered arm has a playbook home (COVERAGE both ways — not a bijection,
since families collapse onto shared arms); the §11 marker splice is between-markers-only / literal /
first-pair-only; and the status matrix renders every arm with the honest gate-vs-scoreboard framing.
Fully offline — the registry + the map are pure, the splice/render take synthetic inputs, and
evaluate_arm's journal I/O is monkeypatched."""

from __future__ import annotations

import scripts.playbook_status as pb
from scripts.strategy_campaign import real_arms

START, END = pb.PLAYBOOK_START, pb.PLAYBOOK_END


# ── parity: the load-bearing guarantee ─────────────────────────────────────────────────────────
def test_every_registered_arm_has_a_playbook_family():
    mapped = pb.mapped_arms()
    for arm in real_arms():
        assert pb.families_for(arm), f"{arm} is registered but has no PLAYBOOK_FAMILIES entry"
        assert arm in mapped


def test_every_mapped_arm_is_registered():
    arms = set(real_arms())
    for fam in pb.PLAYBOOK_FAMILIES:
        for a in fam["arms"]:
            assert a in arms, f"playbook family #{fam['num']} maps to unregistered arm {a!r}"


def test_family_numbers_unique_and_cover_top_10():
    nums = [f["num"] for f in pb.PLAYBOOK_FAMILIES]
    assert len(nums) == len(set(nums))  # unique ids
    assert sorted(nums) == list(range(1, 11))  # exactly the ranked Top-10 (§3)


def test_families_for_is_the_inverse_map():
    for fam in pb.PLAYBOOK_FAMILIES:
        for a in fam["arms"]:
            assert fam["num"] in pb.families_for(a)


# ── §11 marker splice ──────────────────────────────────────────────────────────────────────────
def test_splice_replaces_only_between_markers_and_preserves_prose():
    doc = f"# Playbook\n\nBEFORE\n\n{START}\n\n_old_\n\n{END}\n\nAFTER\n"
    out = pb.splice_playbook(doc, f"{START}\nNEW MATRIX\n{END}")
    assert "BEFORE" in out and "AFTER" in out
    assert "_old_" not in out and "NEW MATRIX" in out
    assert out.count(START) == 1 and out.count(END) == 1


def test_splice_appends_when_no_markers():
    out = pb.splice_playbook("# Doc\n\nbody", f"{START}\nX\n{END}")
    assert out.count(START) == 1 and "body" in out and out.rstrip().endswith(END)


def test_splice_first_pair_only_and_literal_block():
    block = f"{START}\nliteral \\1 and \\g<0> and $5.00\n{END}"
    doc = f"A\n{START}\nOLD1\n{END}\nMID\n{START}\nOLD2\n{END}\nZ"
    out = pb.splice_playbook(doc, block)
    assert "literal \\1 and \\g<0> and $5.00" in out
    assert "OLD1" not in out and "OLD2" in out


# ── matrix render ────────────────────────────────────────────────────────────────────────────────
def _results():
    return [
        {
            "arm": "momentum_adaptive",
            "families": [1],
            "registered": True,
            "survival": {"passed": True, "worst_week_dd": 0.18},
            "stability": {"grade": "FRAGILE"},
            "perf": {"total_return": 0.42, "win_rate": 0.6},
            "forward": {"status": "insufficient forward data"},
            "fwd_perf": {"status": "accruing"},
            "fwd_src": "prod",
        },
        {
            "arm": "dual_momentum",
            "families": [3],
            "registered": True,
            "survival": {"passed": True, "worst_week_dd": 0.115},
            "stability": {"grade": "ROBUST"},
            "perf": {"total_return": 0.31, "win_rate": 0.55},
            "forward": {"status": "evaluated", "forward_eligible": True},
            "fwd_perf": {"status": "evaluated", "win_rate": 1.0, "wins": 11, "decided": 11},
            "fwd_src": "isolated",
        },
    ]


def test_render_matrix_lists_arms_ordered_by_rank_with_honest_framing():
    block = pb.render_matrix(_results(), now_iso="2026-06-14T00:00:00+00:00")
    assert block.startswith(START) and block.rstrip().endswith(END)
    assert "`momentum_adaptive`" in block and "`dual_momentum`" in block
    assert "regime luck, not edge" in block
    assert "Win-rate (window)" in block and "Win-rate (day)" in block
    assert "+42.0%" in block and "100% (11/11d)" in block
    assert "✅ eligible (isolated)" in block
    # ordered by playbook rank: family #1 before family #3
    assert block.index("momentum_adaptive") < block.index("dual_momentum")


def test_evaluate_arm_uses_injected_verdicts_and_handles_empty_track(monkeypatch):
    monkeypatch.setattr(pb, "_read_isolated_rows", lambda arm: [])  # no isolated file
    vmap = {
        "grid": {
            "survival": {"passed": True, "worst_week_dd": 0.216},
            "perf": {"total_return": -0.1, "win_rate": 0.4},
        }
    }
    r = pb.evaluate_arm(
        "grid", vmap=vmap, gmap={"grid": {"grade": "FRAGILE"}}, prod_rows=[], min_days=5.0
    )
    assert r["families"] == [5] and r["stability"]["grade"] == "FRAGILE"
    assert r["perf"]["total_return"] == -0.1
    assert r["forward"]["status"] == "insufficient forward data"  # empty rows
    assert r["fwd_perf"]["status"] == "none"  # no NAV curve
