"""UI-controlled strategy selector (runtime/strategy_select.py): validation,
case-insensitive canonical resolution, and degrade-to-default on every failure mode."""

from __future__ import annotations

import json

import pytest

from ictbot.runtime import strategy_select

DEFAULT = "momentum_adaptive"


@pytest.fixture
def select_file(tmp_path, monkeypatch):
    f = tmp_path / "strategy_select.json"
    monkeypatch.setattr(strategy_select, "STRATEGY_SELECT_FILE", f)
    return f


# ------------------------------ save/load ---------------------------------- #
def test_round_trip(select_file):
    saved = strategy_select.save("dual_momentum")
    assert saved == "dual_momentum"
    assert strategy_select.load(DEFAULT) == "dual_momentum"


def test_save_is_case_insensitive_but_returns_canonical(select_file):
    saved = strategy_select.save("Dual_Momentum")
    assert saved == "dual_momentum"  # canonical lowercase registry name
    assert strategy_select.load(DEFAULT) == "dual_momentum"


def test_save_unknown_raises_and_persists_nothing(select_file):
    with pytest.raises(ValueError, match="unknown strategy"):
        strategy_select.save("does_not_exist")
    assert not select_file.exists()


def test_save_leaves_no_tmp_file(select_file):
    strategy_select.save("rotation")
    leftovers = [p for p in select_file.parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


# ------------------------------ degraded loads ----------------------------- #
def test_load_absent_returns_default(select_file):
    assert strategy_select.load(DEFAULT) == DEFAULT


def test_load_corrupt_returns_default(select_file):
    select_file.write_text("{not json", encoding="utf-8")
    assert strategy_select.load(DEFAULT) == DEFAULT


def test_load_unknown_name_returns_default(select_file):
    select_file.write_text(json.dumps({"strategy": "ghost"}), encoding="utf-8")
    assert strategy_select.load(DEFAULT) == DEFAULT


def test_available_lists_registered():
    avail = strategy_select.available()
    assert "momentum_adaptive" in avail and "breakout" in avail


# --------------- SIM-only enforcement (contest safety) --------------------- #
def test_sim_only_dispatch_resolution(select_file, monkeypatch):
    """The dashboard selector applies to SIM only; LIVE always uses the default."""
    import scripts.run_allocator as ra

    monkeypatch.setattr(ra.settings, "strategy_name", "", raising=False)
    monkeypatch.setattr(ra.settings, "alloc_adaptive", True, raising=False)
    strategy_select.save("dual_momentum")  # operator picks a non-default on the dashboard

    assert ra._resolve_strategy_name("sim") == "dual_momentum"  # SIM honors the file
    assert ra._resolve_strategy_name("live") == "momentum_adaptive"  # LIVE ignores it


def test_live_ignores_file_even_when_set(select_file, monkeypatch):
    import scripts.run_allocator as ra

    monkeypatch.setattr(ra.settings, "strategy_name", "", raising=False)
    monkeypatch.setattr(ra.settings, "alloc_adaptive", True, raising=False)
    select_file.write_text(json.dumps({"strategy": "breakout"}), encoding="utf-8")
    assert ra._resolve_strategy_name("live") == "momentum_adaptive"
