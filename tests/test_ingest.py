"""
/api/ingest/snapshot — the token-gated live-dashboard push endpoint + the read-path override.

The endpoint is the ONE write on the otherwise read-only deploy, so these tests pin its lockdown
(default-deny without a token, constant-time auth, size/shape guards) AND the /api/snapshot override
(serve a fresh pushed snapshot, fall back to the baked journal when absent/stale). No chain, no network.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient transport

from fastapi.testclient import TestClient  # noqa: E402

import ictbot.api.reads as reads  # noqa: E402
from ictbot.settings import settings  # noqa: E402


@pytest.fixture
def client():
    from ictbot.api.app import app

    return TestClient(app)


@pytest.fixture
def pushed_tmp(tmp_path, monkeypatch):
    """Isolate the override file so tests never touch the real data dir."""
    p = tmp_path / "_pushed_snapshot.json"
    monkeypatch.setattr(reads, "PUSHED", p)
    return p


def _snap(served_at: str | None = None, nav=7.93) -> dict:
    """A minimal but shape-valid snapshot payload (carries the required served_at + nav)."""
    return {"served_at": served_at or datetime.now(timezone.utc).isoformat(), "nav": {"current_nav": nav}}


# ------------------------------- auth / default-deny ------------------------------- #
def test_ingest_disabled_when_no_token(client, monkeypatch, pushed_tmp):
    monkeypatch.setattr(settings, "ingest_token", "")
    r = client.post("/api/ingest/snapshot", json=_snap())
    assert r.status_code == 503 and r.json()["ok"] is False
    assert not pushed_tmp.exists()  # default-deny: nothing written


def test_ingest_rejects_missing_token(client, monkeypatch, pushed_tmp):
    monkeypatch.setattr(settings, "ingest_token", "s3cret")
    r = client.post("/api/ingest/snapshot", json=_snap())
    assert r.status_code == 401 and not pushed_tmp.exists()


def test_ingest_rejects_wrong_token(client, monkeypatch, pushed_tmp):
    monkeypatch.setattr(settings, "ingest_token", "s3cret")
    r = client.post("/api/ingest/snapshot", json=_snap(), headers={"X-Ingest-Token": "nope"})
    assert r.status_code == 401 and not pushed_tmp.exists()


# ------------------------------- happy path + guards ------------------------------- #
def test_ingest_stores_with_valid_token(client, monkeypatch, pushed_tmp):
    monkeypatch.setattr(settings, "ingest_token", "s3cret")
    snap = _snap(nav=8.25)
    r = client.post("/api/ingest/snapshot", json=snap, headers={"X-Ingest-Token": "s3cret"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert pushed_tmp.exists()
    stored = json.loads(pushed_tmp.read_text())
    assert stored["nav"]["current_nav"] == 8.25


def test_ingest_rejects_non_snapshot_body(client, monkeypatch, pushed_tmp):
    monkeypatch.setattr(settings, "ingest_token", "s3cret")
    # valid JSON but not a snapshot (no served_at/nav)
    r = client.post("/api/ingest/snapshot", json={"hello": "world"}, headers={"X-Ingest-Token": "s3cret"})
    assert r.status_code == 400 and not pushed_tmp.exists()


def test_ingest_rejects_oversized_body(client, monkeypatch, pushed_tmp):
    monkeypatch.setattr(settings, "ingest_token", "s3cret")
    big = _snap()
    big["filler"] = "x" * 2_100_000  # > 2 MB cap
    r = client.post("/api/ingest/snapshot", json=big, headers={"X-Ingest-Token": "s3cret"})
    assert r.status_code == 413 and not pushed_tmp.exists()


# ------------------------------- read-path override + TTL -------------------------- #
def test_pushed_snapshot_fresh_then_stale(pushed_tmp, monkeypatch):
    monkeypatch.setattr(settings, "pushed_snapshot_ttl_h", 48.0)
    # absent → None
    assert reads.pushed_snapshot() is None
    # fresh → returned
    reads.store_pushed_snapshot(_snap(nav=9.99))
    got = reads.pushed_snapshot()
    assert got is not None and got["nav"]["current_nav"] == 9.99
    # stale (served_at older than TTL) → None (falls back to baked seed)
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    reads.store_pushed_snapshot(_snap(served_at=old))
    assert reads.pushed_snapshot() is None


def test_snapshot_route_serves_pushed_override(client, monkeypatch):
    # Build a schema-valid snapshot from the real generator, stamp a recognizable served_at, and
    # have the override return it — /api/snapshot must serve THAT, not the journal read.
    marker = "2099-01-01T00:00:00+00:00"
    base = reads.snapshot()
    base["served_at"] = marker
    monkeypatch.setattr(reads, "pushed_snapshot", lambda: base)
    r = client.get("/api/snapshot")
    assert r.status_code == 200 and r.json()["served_at"] == marker


def test_snapshot_route_falls_back_when_no_push(client, monkeypatch):
    monkeypatch.setattr(reads, "pushed_snapshot", lambda: None)
    r = client.get("/api/snapshot")
    assert r.status_code == 200 and "served_at" in r.json()  # journal-based snapshot still served
