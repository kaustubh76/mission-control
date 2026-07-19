"""
Scalability layer tests — the read-tier cache + pluggable snapshot store (SCALABILITY.md §4/§5)
and the write-tier cross-host lease backstop (§6).

All new behavior defaults to the single-instance, zero-dependency path the contest runs on, so
these tests pin BOTH the defaults (unchanged behavior) and the opt-in scale paths.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ictbot.api.app as app_module
import ictbot.api.reads as reads

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Minimal in-memory redis stand-in (SET [NX] [EX/PX] / GET / EVAL compare-and-del)."""

    _store: dict = {}

    @classmethod
    def from_url(cls, _url, **_kw):
        return cls()

    def set(self, key, val, nx=False, px=None, ex=None):
        if nx and key in _FakeRedis._store:
            return None
        _FakeRedis._store[key] = val
        return True

    def get(self, key):
        return _FakeRedis._store.get(key)

    def eval(self, _script, _numkeys, key, val):
        if _FakeRedis._store.get(key) == val:
            _FakeRedis._store.pop(key, None)
            return 1
        return 0


@pytest.fixture
def fake_redis(monkeypatch):
    """Inject a fake `redis` module so the lazy `import redis` inside the code paths resolves."""
    _FakeRedis._store = {}
    mod = types.ModuleType("redis")
    mod.Redis = _FakeRedis
    monkeypatch.setitem(sys.modules, "redis", mod)
    monkeypatch.setattr(reads, "_redis_client", None, raising=False)  # drop cached client
    return mod


@pytest.fixture
def client():
    return TestClient(app_module.app)


# --------------------------------------------------------------------------- #
# A1/A4 — edge cache headers + ETag/304 on the read surface
# --------------------------------------------------------------------------- #
def test_snapshot_carries_edge_cache_headers(client, monkeypatch):
    monkeypatch.setattr(reads.settings, "edge_smaxage_s", 3)
    r = client.get("/api/snapshot")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "s-maxage=3" in cc and "stale-while-revalidate=27" in cc
    # CDN-* variants so a Vercel/Cloudflare edge honors it even if Cache-Control is reused/stripped.
    assert "s-maxage=3" in r.headers.get("cdn-cache-control", "")
    assert "s-maxage=3" in r.headers.get("vercel-cdn-cache-control", "")


def test_per_path_ttl_override(client, monkeypatch):
    monkeypatch.setattr(reads.settings, "edge_smaxage_s", 3)
    assert "s-maxage=30" in client.get("/api/market-intel").headers.get("cache-control", "")
    assert "s-maxage=15" in client.get("/api/cmc-api").headers.get("cache-control", "")


def test_live_probe_is_never_cached(client):
    # /api/agent-hub/ping makes a real MCP call at request time — must always reach origin.
    assert client.get("/api/agent-hub/ping").headers.get("cache-control") is None


def test_writes_are_never_cached(client):
    # POST /api/ingest/snapshot with no token → 503, and carries no cache header (non-GET path).
    r = client.post("/api/ingest/snapshot", json={"served_at": "x", "nav": {}})
    assert r.headers.get("cache-control") is None


def test_edge_smaxage_zero_disables_headers(client, monkeypatch):
    monkeypatch.setattr(reads.settings, "edge_smaxage_s", 0)
    assert client.get("/api/health").headers.get("cache-control") is None


def test_snapshot_etag_and_304(client, monkeypatch):
    # A fresh-enough micro-cache window so the two reads return the byte-identical body (same ETag).
    monkeypatch.setattr(reads.settings, "snapshot_cache_ttl_s", 60.0)
    reads.invalidate_snapshot_cache()
    r = client.get("/api/snapshot")
    etag = r.headers.get("etag")
    assert etag and etag.startswith('W/"')
    r2 = client.get("/api/snapshot", headers={"if-none-match": etag})
    assert r2.status_code == 304
    assert r2.headers.get("etag") == etag
    assert not r2.content  # 304 has no body


# --------------------------------------------------------------------------- #
# A2/A3 — in-process micro-cache (single-flight + serve-stale) + invalidation
# --------------------------------------------------------------------------- #
def test_micro_cache_holds_within_ttl(monkeypatch):
    calls = {"n": 0}

    def _fake():
        calls["n"] += 1
        return {"served_at": f"build-{calls['n']}", "nav": {}}

    monkeypatch.setattr(reads, "snapshot", _fake)
    monkeypatch.setattr(reads.settings, "snapshot_cache_ttl_s", 60.0)
    reads.invalidate_snapshot_cache()
    a = reads.snapshot_cached()
    b = reads.snapshot_cached()
    assert a is b and calls["n"] == 1  # one build serves both


def test_invalidate_forces_rebuild(monkeypatch):
    calls = {"n": 0}

    def _fake():
        calls["n"] += 1
        return {"served_at": f"build-{calls['n']}", "nav": {}}

    monkeypatch.setattr(reads, "snapshot", _fake)
    monkeypatch.setattr(reads.settings, "snapshot_cache_ttl_s", 60.0)
    reads.invalidate_snapshot_cache()
    reads.snapshot_cached()
    reads.invalidate_snapshot_cache()
    again = reads.snapshot_cached()
    assert calls["n"] == 2 and again["served_at"] == "build-2"


def test_micro_cache_ttl_zero_disables(monkeypatch):
    calls = {"n": 0}

    def _fake():
        calls["n"] += 1
        return {"served_at": "x", "nav": {}}

    monkeypatch.setattr(reads, "snapshot", _fake)
    monkeypatch.setattr(reads.settings, "snapshot_cache_ttl_s", 0.0)
    reads.invalidate_snapshot_cache()
    reads.snapshot_cached()
    reads.snapshot_cached()
    assert calls["n"] == 2  # every call rebuilds


# --------------------------------------------------------------------------- #
# B — pluggable pushed-snapshot store (file default | redis shared)
# --------------------------------------------------------------------------- #
def _fresh_snapshot() -> dict:
    return {"served_at": datetime.now(timezone.utc).isoformat(), "nav": {"current_nav": 1.0}}


def test_file_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(reads, "PUSHED", tmp_path / "_pushed_snapshot.json")
    monkeypatch.setattr(reads.settings, "snapshot_store", "file")
    snap = _fresh_snapshot()
    reads.store_pushed_snapshot(snap)
    assert reads.pushed_snapshot()["nav"]["current_nav"] == 1.0
    assert not list(tmp_path.glob("*.tmp"))  # atomic write left no tmp file


def test_file_store_respects_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(reads, "PUSHED", tmp_path / "_pushed_snapshot.json")
    monkeypatch.setattr(reads.settings, "snapshot_store", "file")
    monkeypatch.setattr(reads.settings, "pushed_snapshot_ttl_h", 1.0)
    stale = {"served_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(), "nav": {}}
    reads.store_pushed_snapshot(stale)
    assert reads.pushed_snapshot() is None  # past TTL → falls back to the baked seed


def test_redis_store_round_trip(fake_redis, monkeypatch):
    monkeypatch.setattr(reads.settings, "snapshot_store", "redis")
    monkeypatch.setattr(reads.settings, "redis_url", "redis://fake:6379/0")
    snap = _fresh_snapshot()
    reads.store_pushed_snapshot(snap)
    assert reads._PUSHED_REDIS_KEY in _FakeRedis._store  # written to the shared key
    assert reads.pushed_snapshot()["nav"]["current_nav"] == 1.0  # read back from redis


def test_redis_selected_but_unreachable_falls_through_to_file(tmp_path, monkeypatch):
    # backend=redis but no client (no url / import fails) → store must NOT lose the push: it writes
    # the file so a later file-backend read can recover it.
    monkeypatch.setattr(reads, "PUSHED", tmp_path / "_pushed_snapshot.json")
    monkeypatch.setattr(reads.settings, "snapshot_store", "redis")
    monkeypatch.setattr(reads, "_redis", lambda: None)
    reads.store_pushed_snapshot(_fresh_snapshot())
    assert (tmp_path / "_pushed_snapshot.json").exists()


# --------------------------------------------------------------------------- #
# C — write-tier cross-host lease backstop (default flock → no-op sentinel)
# --------------------------------------------------------------------------- #
def _load_run_allocator():
    spec = importlib.util.spec_from_file_location(
        "run_allocator_scaling", REPO_ROOT / "scripts" / "run_allocator.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ra():
    return _load_run_allocator()


def test_lease_is_noop_sentinel_when_flock(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "allocator_lock", "flock")
    token = ra._acquire_redis_lease("live")
    assert token == "flock-only"  # default: redis lease not in use → treated as acquired
    ra._release_redis_lease("live", token)  # must be a clean no-op


def test_lease_refuses_when_redis_selected_without_url(ra, monkeypatch):
    monkeypatch.setattr(ra.settings, "allocator_lock", "redis")
    monkeypatch.setattr(ra.settings, "redis_url", "")
    assert ra._acquire_redis_lease("live") is None  # fail-closed: never tick without the guard


def test_lease_nx_blocks_second_host(ra, fake_redis, monkeypatch):
    monkeypatch.setattr(ra.settings, "allocator_lock", "redis")
    monkeypatch.setattr(ra.settings, "redis_url", "redis://fake:6379/0")
    first = ra._acquire_redis_lease("live")
    assert first and first != "flock-only"
    assert ra._acquire_redis_lease("live") is None  # SET NX fails for a second holder
    ra._release_redis_lease("live", first)  # compare-and-del frees it
    assert ra._acquire_redis_lease("live")  # re-acquirable after release
