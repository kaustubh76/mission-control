"""POST /api/controls/tokens — the UI token-toggle endpoint.

Gated on fastapi being installed (it lives in the `[api]` extra, not `[dev]`),
mirroring how test_app_import.py skips on missing streamlit.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient transport

from fastapi.testclient import TestClient  # noqa: E402

from ictbot.runtime import active_tokens  # noqa: E402
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(active_tokens, "ACTIVE_TOKENS_FILE", tmp_path / "active_tokens.json")
    # Pin the active-token floor to 2 so these endpoint tests use small subsets
    # independent of the production top_k default (which raises min-active).
    monkeypatch.setattr(active_tokens.settings, "alloc_top_k", 2)
    from ictbot.api.app import app

    return TestClient(app)


def test_post_valid_subset_persists(client):
    r = client.post("/api/controls/tokens", json={"active": ["DOGE", "BNB", "CAKE"]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["active"] == ["BNB", "CAKE", "DOGE"]  # canonical order
    assert "3/8" in body["message"]
    assert active_tokens.load() == ["BNB", "CAKE", "DOGE"]


def test_post_too_few_rejected_with_current_list(client):
    active_tokens.save(["BNB", "ETH", "CAKE"])
    r = client.post("/api/controls/tokens", json={"active": ["BNB"]})
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["active"] == ["BNB", "ETH", "CAKE"]  # unchanged current state
    assert "at least 2" in body["message"]


def test_post_unknown_token_rejected(client):
    r = client.post("/api/controls/tokens", json={"active": ["BNB", "XRP"]})
    assert r.status_code == 400
    assert "unknown token" in r.json()["message"]


def test_post_works_while_kill_engaged(client, monkeypatch):
    # Config, not a trade — documented as NOT kill-gated.
    from ictbot.runtime import kill_switch

    monkeypatch.setattr(kill_switch, "is_engaged", lambda: True)
    r = client.post("/api/controls/tokens", json={"active": ["BNB", "ETH"]})
    assert r.status_code == 200


def test_strategy_endpoint_reflects_active(client):
    client.post("/api/controls/tokens", json={"active": ["BNB", "ETH", "LINK"]})
    r = client.get("/api/strategy")
    assert r.status_code == 200
    body = r.json()
    assert body["tokens"] == list(CONTEST_TOKENS)
    assert body["active"] == ["BNB", "ETH", "LINK"]
    assert "of 3" in body["summary"]
