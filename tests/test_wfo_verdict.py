"""
Regression test for ictbot.engine.wfo.classify().

Locks in the rule that "✅ holds" requires BOTH halves positive — the
bug surfaced in docs/findings.md §12 where a TRAIN-losing config got
labelled "holds" just because TEST happened to cross zero in the
right direction.
"""

from ictbot.engine.wfo import classify


def test_no_edge_when_train_is_negative():
    # The original bug: TEST positive but TRAIN negative.
    assert classify(-0.27, +0.53) == "no edge"


def test_no_edge_when_train_is_exactly_zero():
    assert classify(0.0, +1.5) == "no edge"


def test_no_edge_when_train_is_missing():
    assert classify(None, +0.5) == "no edge"


def test_holds_when_both_halves_positive():
    assert classify(+1.0, +0.5) == "✅ holds"
    assert classify(+0.01, +0.01) == "✅ holds"


def test_overfit_when_train_positive_but_test_negative():
    assert classify(+2.5, -0.3) == "❌ overfit"


def test_overfit_when_train_positive_and_test_exactly_zero():
    # Zero test = no real net edge, not "holds".
    assert classify(+1.0, 0.0) == "❌ overfit"


def test_no_closures_when_test_expectancy_is_none():
    # Trumps the train-positive check — we can't classify without TEST data.
    assert classify(+1.0, None) == "no closures"


def test_no_edge_trumps_no_closures_when_train_is_already_bad():
    # If TRAIN ≤ 0 we don't need TEST to know there's nothing here.
    assert classify(-1.0, None) == "no edge"


# ----------------------------------------------------------------------
# F3 — small-sample gate. Verdict "✅ holds" requires test_closures
# ≥ min_closures (default 10). Catches the PAXG case from findings §15
# where W/L=2/6 (n=8) misclassified as "holds".
# ----------------------------------------------------------------------


def test_small_sample_when_test_closures_below_default_threshold():
    assert classify(+1.0, +0.5, test_closures=8) == "small sample"
    assert classify(+1.0, +0.5, test_closures=9) == "small sample"


def test_holds_when_test_closures_at_or_above_threshold():
    assert classify(+1.0, +0.5, test_closures=10) == "✅ holds"
    assert classify(+1.0, +0.5, test_closures=25) == "✅ holds"


def test_small_sample_gate_off_when_closures_not_supplied():
    # Backwards compat: callers that don't pass test_closures get the
    # old two-arg behaviour. New code in print_report/print_scoreboard
    # always supplies it.
    assert classify(+1.0, +0.5) == "✅ holds"


def test_small_sample_gate_does_not_promote_overfit_to_holds():
    # If TEST exp ≤ 0, verdict is "❌ overfit" regardless of n.
    assert classify(+1.0, -0.3, test_closures=3) == "❌ overfit"


def test_small_sample_gate_does_not_promote_no_edge_to_holds():
    assert classify(-0.5, +0.5, test_closures=3) == "no edge"


def test_min_closures_override():
    # Caller can demand a tighter bar.
    assert classify(+1.0, +0.5, test_closures=15, min_closures=20) == "small sample"
    assert classify(+1.0, +0.5, test_closures=15, min_closures=10) == "✅ holds"
