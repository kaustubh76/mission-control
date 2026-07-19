"""POST /api/commerce/preview — the READ-ONLY "preview the report this agent sells" control.

Unlike create-job/settle, preview signs NOTHING, so it is UNGUARDED: it must work everywhere,
including the zero-secret cloud deploy (where buyer_available() is False). These tests monkeypatch
agent.regime_report.build_report so they never touch CMC/the network, and assert: (1) no 403 even
without keys, (2) the report fields survive the response model, (3) a degraded build is surfaced as
ok=True/status=degraded, (4) the buyer query flows through, (5) it never 500s.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient transport

from fastapi.testclient import TestClient  # noqa: E402

from ictbot.agent import commerce, regime_report  # noqa: E402


@pytest.fixture
def client():
    from ictbot.api.app import app

    return TestClient(app)


_OK_REPORT = {
    "schema": "cmc-regime-report/v1",
    "status": "ok",
    "issuer": "RegimeAdaptiveMomentumAgent",
    "strategy": "momentum_cmc",
    "regime_score": 0.32,
    "deploy_cap": 0.49,
    "ta_health": 0.44,
    "fear_greed": 21,
    "momentum_ranking": ["CAKE", "ETH", "BNB"],
    "target_weights": {"CAKE": 0.2, "ETH": 0.15},
    "rationale": "Extreme fear; capping deployment.",
    "cmc_sources": {"pro_api": ["x"], "mcp_skill": {"name": "market_overview"}, "mcp_ta": "y"},
    "ts": "2026-06-20T12:00:00Z",
}


def test_preview_works_without_keys(client, monkeypatch):
    # The crux: the cloud deploy has NO signing key (buyer_available False), yet preview must still
    # return the genuine deliverable — it signs nothing, so it is never gated to 403.
    monkeypatch.setattr(commerce, "buyer_available", lambda: False)
    monkeypatch.setattr(regime_report, "build_report", lambda *a, **k: dict(_OK_REPORT))
    r = client.post("/api/commerce/preview", json={"description": "regime read"})
    assert r.status_code == 200  # NOT 403
    body = r.json()
    assert body["ok"] is True and body["status"] == "ok"
    # The SDK→trading lineage fields must survive the response model so the UI can show them.
    assert body["regime_score"] == 0.32 and body["deploy_cap"] == 0.49 and body["ta_health"] == 0.44
    assert body["momentum_ranking"] == ["CAKE", "ETH", "BNB"]
    assert body["cmc_sources"]["mcp_ta"] == "y"  # buyer-verifiable CMC provenance preserved


def test_preview_passes_query_through(client, monkeypatch):
    seen = {}

    def _capture(*a, query=None, **k):
        seen["query"] = query
        return dict(_OK_REPORT)

    monkeypatch.setattr(regime_report, "build_report", _capture)
    client.post("/api/commerce/preview", json={"description": "what's your regime read?"})
    assert seen["query"] == "what's your regime read?"


def test_preview_degraded_is_ok_true(client, monkeypatch):
    # A data miss → build_report returns a well-formed degraded report; the endpoint surfaces it as
    # ok=True/status=degraded with the reason in `message` (not an error).
    degraded = {
        "status": "degraded",
        "reason": "insufficient CMC candle history",
        "regime_score": None,
        "deploy_cap": None,
        "momentum_ranking": [],
        "target_weights": {},
    }
    monkeypatch.setattr(regime_report, "build_report", lambda *a, **k: degraded)
    r = client.post("/api/commerce/preview", json={"description": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["status"] == "degraded"
    assert body["momentum_ranking"] == [] and "insufficient" in body["message"]


def test_preview_never_500(client, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("cmc down")

    monkeypatch.setattr(regime_report, "build_report", _boom)
    r = client.post("/api/commerce/preview", json={"description": "x"})
    assert r.status_code == 200  # defensive: surfaced as ok:false, never crashes the server
    body = r.json()
    assert body["ok"] is False and "cmc down" in body["message"]
