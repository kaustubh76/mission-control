"""
B2 (ROADMAP §B2) — ATR-scaled stop grid.

Verifies:
  - GRIDS["atr"] exists with stop_mode="atr"
  - 6 (sl_atr, tp_atr) × 4 poi × 2 fvg = 48 combos
  - run_sweep forwards combo values as sl_atr_mult/tp_atr_mult, NOT sl_frac/tp_frac
  - run_wfo._run_one honours stop_mode="atr"
  - Default grids stay on stop_mode="fraction" (no regression)
"""

from ictbot.engine import sweep, wfo
from ictbot.engine.sweep import GRIDS, _iter_combos


def _fake_report():
    return {
        "pair": "TEST",
        "bars_scanned": 0,
        "counts": {},
        "signals": [],
        "near_misses": [],
        "verbose": False,
    }


def test_atr_grid_exists_with_stop_mode_atr():
    g = GRIDS["atr"]
    assert g["stop_mode"] == "atr"


def test_atr_grid_combo_count():
    combos = _iter_combos(GRIDS["atr"])
    assert len(combos) == 48


def test_default_grid_implicitly_fraction_mode():
    # Backwards compat: grids without stop_mode default to "fraction".
    g = GRIDS["default"]
    assert g.get("stop_mode", "fraction") == "fraction"


def test_atr_combos_values_look_like_multipliers_not_fractions():
    # ATR multipliers are >= 0.5; price fractions are < 0.05.
    for sl, tp in GRIDS["atr"]["sl_tp"]:
        assert sl >= 0.5, f"sl={sl} looks like a price fraction, not an ATR mult"
        assert tp >= 1.0, f"tp={tp} looks like a price fraction, not an ATR mult"


def test_run_sweep_forwards_atr_mults_when_stop_mode_atr(monkeypatch):
    captured = []

    def fake_run_backtest(pair, bars, *, verbose, quiet, **kw):
        captured.append(kw)
        return _fake_report()

    monkeypatch.setattr(sweep, "run_backtest", fake_run_backtest)
    sweep.run_sweep("BTC/USDT:USDT", 100, GRIDS["atr"])

    assert captured, "no combos ran"
    # Every call should carry sl_atr_mult/tp_atr_mult and NOT sl_frac/tp_frac.
    for kw in captured:
        assert "sl_atr_mult" in kw
        assert "tp_atr_mult" in kw
        assert "sl_frac" not in kw
        assert "tp_frac" not in kw


def test_run_sweep_forwards_fractions_when_stop_mode_absent(monkeypatch):
    captured = []

    def fake_run_backtest(pair, bars, *, verbose, quiet, **kw):
        captured.append(kw)
        return _fake_report()

    monkeypatch.setattr(sweep, "run_backtest", fake_run_backtest)
    # GRIDS["quick"] has no stop_mode → must default to fraction.
    sweep.run_sweep("BTC/USDT:USDT", 100, GRIDS["quick"])

    assert captured
    for kw in captured:
        assert "sl_frac" in kw
        assert "tp_frac" in kw
        assert "sl_atr_mult" not in kw
        assert "tp_atr_mult" not in kw


def test_wfo_run_one_routes_atr_to_atr_mult_kwargs(monkeypatch):
    captured = {}

    def fake_run_backtest(pair, **kw):
        captured.update(kw)
        return _fake_report()

    monkeypatch.setattr(wfo, "run_backtest", fake_run_backtest)
    wfo._run_one(
        "BTC/USDT:USDT",
        history={},
        start=0,
        end=100,
        poi_tol=0.003,
        sl=1.0,
        tp=3.0,
        fvg=False,
        stop_mode="atr",
    )
    assert captured.get("sl_atr_mult") == 1.0
    assert captured.get("tp_atr_mult") == 3.0
    assert "sl_frac" not in captured
    assert "tp_frac" not in captured


def test_wfo_run_one_defaults_to_fraction_mode(monkeypatch):
    captured = {}

    def fake_run_backtest(pair, **kw):
        captured.update(kw)
        return _fake_report()

    monkeypatch.setattr(wfo, "run_backtest", fake_run_backtest)
    wfo._run_one(
        "BTC/USDT:USDT",
        history={},
        start=0,
        end=100,
        poi_tol=0.003,
        sl=0.005,
        tp=0.015,
        fvg=False,
    )
    assert captured.get("sl_frac") == 0.005
    assert captured.get("tp_frac") == 0.015
    assert "sl_atr_mult" not in captured
    assert "tp_atr_mult" not in captured
