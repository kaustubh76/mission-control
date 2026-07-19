"""
Tests for the clean live-arm switch: run_allocator --strategy NAME + the _assert_live_arm_gate
survival guard (so an operator can run any survival-gated arm live, but never a DQ-unsafe one).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_allocator.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_allocator_armswitch", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ra(tmp_path, monkeypatch):
    """run_allocator module with the gate-file root redirected into a tmp dir.
    _assert_live_arm_gate reads JOURNAL_DIR.parent / reports / strategy_gates.json."""
    mod = _load()
    monkeypatch.setattr(mod, "JOURNAL_DIR", tmp_path / "journal")
    return mod


def _write_gates(tmp_path: Path, gates: dict) -> None:
    rep = tmp_path / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "strategy_gates.json").write_text(json.dumps(gates))


# --------------------------- _assert_live_arm_gate: three branches --------------------------- #
def test_gate_refuses_survival_failed_arm(ra, tmp_path):
    _write_gates(tmp_path, {"badarm": {"survival": {"passed": False, "worst_week_dd": 0.40}}})
    assert ra._assert_live_arm_gate("badarm") == 2  # DQ-unsafe → REFUSE


def test_gate_warns_but_allows_forward_pending(ra, tmp_path, capsys):
    _write_gates(
        tmp_path,
        {"mean_reversion": {"survival": {"passed": True, "worst_week_dd": 0.132},
                            "forward": {"forward_eligible": False}}},
    )
    assert ra._assert_live_arm_gate("mean_reversion") is None  # survival ✓ → proceed
    assert "forward gate NOT eligible" in capsys.readouterr().out  # ...but loudly warned


def test_gate_clean_for_fully_cleared_arm(ra, tmp_path, capsys):
    _write_gates(
        tmp_path,
        {"good": {"survival": {"passed": True}, "forward": {"forward_eligible": True}}},
    )
    assert ra._assert_live_arm_gate("good") is None
    out = capsys.readouterr().out
    assert "NOT eligible" not in out and "REFUSING" not in out


def test_gate_warns_but_allows_when_gatefile_absent(ra, capsys):
    # No strategy_gates.json on this host (gitignored/campaign-only) → proceed UNVERIFIED, never block.
    assert ra._assert_live_arm_gate("whatever") is None
    assert "UNVERIFIED" in capsys.readouterr().out


# --------------------------- --strategy resolves the live arm --------------------------- #
def test_strategy_override_resolves_live_arm(ra, monkeypatch):
    """main() sets settings.strategy_name from --strategy; LIVE resolution returns it verbatim
    (the dashboard selector is ignored for live)."""
    monkeypatch.setattr(ra.settings, "strategy_name", "mean_reversion")
    assert ra._resolve_strategy_name("live") == "mean_reversion"
