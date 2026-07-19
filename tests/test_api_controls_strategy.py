"""POST /api/controls/strategy — the SIM-only dashboard strategy selector endpoint."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from ictbot.runtime import strategy_select  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(strategy_select, "STRATEGY_SELECT_FILE", tmp_path / "strategy_select.json")
    from ictbot.api.app import app

    return TestClient(app)


def test_post_valid_strategy_persists(client):
    r = client.post("/api/controls/strategy", json={"strategy": "dual_momentum"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["strategy"] == "dual_momentum"
    assert "dual_momentum" in body["available"]
    assert strategy_select.load("momentum_adaptive") == "dual_momentum"


def test_post_is_case_insensitive(client):
    r = client.post("/api/controls/strategy", json={"strategy": "Breakout"})
    assert r.status_code == 200
    assert r.json()["strategy"] == "breakout"


def test_post_unknown_rejected(client):
    r = client.post("/api/controls/strategy", json={"strategy": "ghost"})
    assert r.status_code == 400
    assert "unknown strategy" in r.json()["message"]


def test_post_works_while_kill_engaged(client, monkeypatch):
    from ictbot.runtime import kill_switch

    monkeypatch.setattr(kill_switch, "is_engaged", lambda: True)
    r = client.post("/api/controls/strategy", json={"strategy": "rotation"})
    assert r.status_code == 200  # config, not a trade — not kill-gated


def test_snapshot_exposes_strategies_block(client):
    client.post("/api/controls/strategy", json={"strategy": "rotation"})
    r = client.get("/api/snapshot")
    assert r.status_code == 200
    strs = r.json()["strategies"]
    assert strs["current"] == "rotation"
    names = {i["name"] for i in strs["items"]}
    assert {"momentum_adaptive", "dual_momentum", "rotation", "breakout"} <= names
