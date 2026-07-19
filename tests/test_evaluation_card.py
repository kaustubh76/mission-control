"""
Dashboard contract for the auto-selector card. web/ has no JS test runner, so we guard the card's data
contract from the Python side: (1) `reads.evaluation_card()` surfaces every field AutoSelectorCard.tsx
reads, and (2) the FastAPI `response_model` (EvaluationOut/HysteresisOut/ArmScoreOut) declares them all —
so Pydantic does NOT silently strip a field on the way out (the response_model footgun).
"""

from __future__ import annotations

import json

import ictbot.api.reads as reads
from ictbot.api.schemas import ArmScoreOut, EvaluationOut, HysteresisOut

# Fields AutoSelectorCard.tsx actually reads (grounded in the component, not guessed).
_CARD_TOP = {"recommended", "current", "action", "hysteresis", "reason", "scores", "apply_cmd",
             "auto_apply_live"}
_CARD_HYST = {"consecutive", "sustain", "margin"}
_CARD_SCORE = {"arm", "score", "dq_safe", "forward_eligible", "stability"}


def _reco(n_scores: int = 3) -> dict:
    """A recommendation file shaped exactly as strategy_evaluator.write_recommendation emits."""
    return {
        "recommended_live_arm": "dual_momentum",
        "current_live_arm": "mean_reversion",
        "action": "SWITCH",
        "switch": True,
        "reason": "dual_momentum beats mean_reversion by ≥0.02 for 2/2 evals",
        "scores": [{"arm": f"arm{i}", "score": -0.1 * i, "dq_safe": True, "forward_eligible": i == 0,
                    "stability": "ROBUST", "verdict": "✅ READY (sign-off)"} for i in range(n_scores)],
        "hysteresis": {"consecutive": 2, "sustain": 2, "margin": 0.02,
                       "incumbent_score": -0.25, "champion_score": 0.05},
        "apply_cmd": "scripts/live_tick.sh dual_momentum",
        "auto_apply_live": False,
        "ts": "2026-06-19T12:00:00+00:00",
    }


def _write_reco(tmp_path, monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(reads, "DATA_DIR", tmp_path)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "strategy_evaluation.json").write_text(
        json.dumps(payload), encoding="utf-8")


def test_card_none_when_evaluator_has_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(reads, "DATA_DIR", tmp_path)  # no file written
    assert reads.evaluation_card() is None


def test_card_surfaces_every_field_the_ui_reads(tmp_path, monkeypatch):
    _write_reco(tmp_path, monkeypatch, _reco())
    c = reads.evaluation_card()
    assert c is not None
    assert _CARD_TOP <= set(c), f"missing UI fields: {_CARD_TOP - set(c)}"
    assert c["recommended"] == "dual_momentum" and c["current"] == "mean_reversion"
    assert c["action"] == "SWITCH" and c["switch"] is True
    assert c["apply_cmd"].endswith("dual_momentum") and c["auto_apply_live"] is False
    assert _CARD_HYST <= set(c["hysteresis"])
    assert all(_CARD_SCORE <= set(s) for s in c["scores"])


def test_card_caps_scores_at_8(tmp_path, monkeypatch):
    _write_reco(tmp_path, monkeypatch, _reco(n_scores=11))
    c = reads.evaluation_card()
    assert len(c["scores"]) == 8  # the UI shows the top 8 only


def test_response_model_does_not_strip_card_fields(tmp_path, monkeypatch):
    """The footgun: a key the card emits but the schema doesn't declare is dropped from the API response.
    Assert every emitted key is a declared field, and a round-trip through the model preserves values."""
    _write_reco(tmp_path, monkeypatch, _reco())
    c = reads.evaluation_card()
    assert set(c) <= set(EvaluationOut.model_fields), \
        f"card emits undeclared fields (stripped by response_model): {set(c) - set(EvaluationOut.model_fields)}"
    assert set(c["hysteresis"]) <= set(HysteresisOut.model_fields), \
        f"hysteresis has undeclared keys: {set(c['hysteresis']) - set(HysteresisOut.model_fields)}"
    for s in c["scores"]:
        assert set(s) <= set(ArmScoreOut.model_fields), \
            f"score row has undeclared keys: {set(s) - set(ArmScoreOut.model_fields)}"

    # Round-trip: model validation + serialization must keep the values the UI binds to.
    dumped = EvaluationOut(**c).model_dump()
    for k in _CARD_TOP:
        assert dumped[k] == c[k]
    assert dumped["switch"] == c["switch"] and dumped["ts"] == c["ts"]
