"""
B1 (ROADMAP §B1) — rr2plus grid acceptance.

Locks in the rule that every combo in GRIDS["rr2plus"] has tp/sl ≥ 2.0
so the grid can never re-introduce the tight-RR friction trap surfaced
in findings §15. Also exercises _iter_combos against both grid shapes
(independent sl×tp and paired sl_tp).
"""

import pytest

from ictbot.engine.sweep import GRIDS, _iter_combos


def test_rr2plus_grid_exists():
    assert "rr2plus" in GRIDS


def test_rr2plus_every_combo_has_rr_at_least_two():
    grid = GRIDS["rr2plus"]
    assert "sl_tp" in grid, "rr2plus must use paired (sl, tp) tuples"
    for sl, tp in grid["sl_tp"]:
        rr = tp / sl
        assert rr >= 2.0, f"(sl={sl}, tp={tp}) has rr={rr:.2f} < 2.0"


def test_rr2plus_grid_combo_count():
    # Spec: 6 (sl,tp) × 4 poi_tol × 2 fvg = 48 combos.
    combos = _iter_combos(GRIDS["rr2plus"])
    assert len(combos) == 48


def test_iter_combos_independent_grid_uses_product():
    # default grid: 4 poi × 3 sl × 3 tp × 2 fvg = 72 combos.
    combos = _iter_combos(GRIDS["default"])
    assert len(combos) == 72


def test_iter_combos_quick_grid_count():
    # quick grid: 2 poi × 2 sl × 2 tp × 2 fvg = 16 combos.
    combos = _iter_combos(GRIDS["quick"])
    assert len(combos) == 16


def test_iter_combos_returns_quadruples():
    combos = _iter_combos(GRIDS["rr2plus"])
    for combo in combos:
        assert len(combo) == 4
        poi, sl, tp, fvg = combo
        assert isinstance(poi, float)
        assert isinstance(sl, float)
        assert isinstance(tp, float)
        assert isinstance(fvg, bool)


def test_iter_combos_sl_tp_overrides_independent_lists():
    # If both sl/tp lists AND sl_tp pairs exist, pairs win — explicit
    # over implicit. (Currently no built-in grid does this; future grids
    # may want to override the default product.)
    grid = {
        "poi_tol": [0.001],
        "sl": [0.999],  # would never appear in output
        "tp": [0.999],  # would never appear in output
        "sl_tp": [(0.005, 0.015)],
        "require_fvg": [True],
    }
    combos = _iter_combos(grid)
    assert len(combos) == 1
    assert combos[0] == (0.001, 0.005, 0.015, True)


@pytest.mark.parametrize("grid_name", sorted(GRIDS.keys()))
def test_every_grid_has_at_least_one_combo(grid_name):
    combos = _iter_combos(GRIDS[grid_name])
    assert len(combos) >= 1
