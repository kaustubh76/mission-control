"""POST /api/commerce/create-job — the operator-local "create a real ERC-8183 job" control.

Gated on fastapi (the `[api]` extra). The real loop signs on-chain, so these tests NEVER touch the
chain: they assert the guard (403 on the read-only deploy) and monkeypatch the orchestrator to
verify the success + precheck response shapes.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient transport

from fastapi.testclient import TestClient  # noqa: E402

from ictbot.agent import commerce  # noqa: E402


@pytest.fixture
def client():
    from ictbot.api.app import app

    return TestClient(app)


def test_create_job_operator_only_403_without_keys(client, monkeypatch):
    # The read-only deploy has no signing key → buyer_available() is False → 403, no signing.
    monkeypatch.setattr(commerce, "buyer_available", lambda: False)
    r = client.post("/api/commerce/create-job", json={"description": "regime read"})
    assert r.status_code == 403
    body = r.json()
    assert body["ok"] is False
    assert "operator-only" in body["message"]


def test_create_job_success_shape(client, monkeypatch):
    monkeypatch.setattr(commerce, "buyer_available", lambda: True)

    def _fake_loop(query, *, amount=None, expiry_min=60):
        assert query == "regime read"  # request body flows through
        return {
            "ok": True, "stage": "served", "job_id": 42, "status": "COMPLETED", "tx": "0xabc",
            "deliverable_hash": "0xdef", "deliverable_url": "ipfs://QmDeliv",
            "buyer": "0xB", "provider": "0xP", "amount": 10000, "token": "U",
        }

    monkeypatch.setattr(commerce, "create_and_serve_job", _fake_loop)
    r = client.post("/api/commerce/create-job", json={"description": "regime read"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["job_id"] == 42 and body["tx"] == "0xabc"
    # The deliverable (IPFS) + the loop stage must survive the response model so the UI can link it.
    assert body["deliverable_url"] == "ipfs://QmDeliv"
    assert body["stage"] == "served"


def test_create_job_insufficient_balance_precheck(client, monkeypatch):
    # The orchestrator returns an actionable fund-precheck dict; buyer/need/have + the loop `stage`
    # survive the response model so the UI can show the funding hint AND distinguish failure modes.
    monkeypatch.setattr(commerce, "buyer_available", lambda: True)
    monkeypatch.setattr(
        commerce, "create_and_serve_job",
        lambda q, **k: {"ok": False, "stage": "fund-precheck", "buyer": "0xB",
                        "token": "U", "need": 10000, "have": 0, "message": "faucet-fund 0xB"},
    )
    r = client.post("/api/commerce/create-job", json={"description": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["need"] == 10000 and body["have"] == 0
    assert body["buyer"] == "0xB"
    assert body["stage"] == "fund-precheck"  # now declared → distinguishes precheck from a loop error


def test_commerce_jobs_surfaces_deliverable_url(tmp_path, monkeypatch):
    # The ledger reader must surface the served job's IPFS deliverable URL (+ hash/tx) so the public
    # dashboard can link straight to the real product — not just show a bare hash.
    from ictbot.api import reads

    journal = tmp_path / "journal"
    journal.mkdir()
    (journal / "commerce_jobs.jsonl").write_text(
        '{"event":"CREATE","job_id":7}\n'
        '{"event":"FUND","job_id":7,"amount":100000000000000000}\n'
        '{"event":"SUBMITTED_ONCHAIN","job_id":7,"tx":"0xfeed",'
        '"deliverable_hash":"0xhash","deliverable_url":"ipfs://QmABC"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(reads, "DATA_DIR", tmp_path)
    out = reads._commerce_jobs()
    assert out["jobs_served"] == 1
    assert out["last_deliverable_url"] == "ipfs://QmABC"
    assert out["last_deliverable_hash"] == "0xhash"
    assert out["last_tx"] == "0xfeed"


# --------------------------------------------------------------------------- #
# SETTLE finalization
# --------------------------------------------------------------------------- #
def test_unsettled_job_ids(tmp_path, monkeypatch):
    journal = tmp_path / "commerce_jobs.jsonl"
    journal.write_text(
        '{"event":"SUBMITTED_ONCHAIN","job_id":1}\n'
        '{"event":"SUBMITTED_ONCHAIN","job_id":2}\n'
        '{"event":"SETTLE","job_id":1,"status":"COMPLETED"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(commerce, "COMMERCE_JOURNAL", journal)
    assert commerce.unsettled_job_ids() == [2]  # job 1 settled, job 2 still pending


def test_settle_pending_jobs_settles_and_defers(tmp_path, monkeypatch):
    # The settle loop must finalize jobs whose window has closed and classify a NotDecided revert
    # (0x17be5b7b) as DEFERRED — not a failure — without touching the real ledger.
    import ictbot.agent.identity as identity

    journal = tmp_path / "commerce_jobs.jsonl"
    monkeypatch.setattr(commerce, "COMMERCE_JOURNAL", journal)
    monkeypatch.setattr(identity, "_lower_sdk_gas_floor", lambda: None)  # no SDK/network in tests
    monkeypatch.setattr(commerce, "_net_name", lambda: "bsc-mainnet")

    class FakeBuyer:
        def settle(self, jid):
            if jid == 999:  # still inside the optimistic dispute window
                raise RuntimeError("execution reverted: ('0x17be5b7b', 'NotDecided')")
            return {"ok": True}

        def get_job_status(self, jid):
            return "COMPLETED"

    monkeypatch.setattr(commerce, "_buyer_client", lambda: FakeBuyer())

    out = commerce.settle_pending_jobs([42, 999])
    assert out["ok"] is True and out["pending_before"] == 2
    assert [s["job_id"] for s in out["settled"]] == [42]
    assert [d["job_id"] for d in out["deferred"]] == [999]
    assert out["errors"] == []
    # journaled: SETTLE for 42, SETTLE_DEFERRED for 999
    evs = {(r["job_id"], r["event"]) for r in (json.loads(x) for x in journal.read_text().splitlines() if x.strip())}
    assert (42, "SETTLE") in evs and (999, "SETTLE_DEFERRED") in evs


def test_commerce_jobs_pending_settle_and_status(tmp_path, monkeypatch):
    from ictbot.api import reads

    journal = tmp_path / "journal"
    journal.mkdir()
    (journal / "commerce_jobs.jsonl").write_text(
        '{"event":"CREATE","job_id":1}\n'
        '{"event":"FUND","job_id":1,"amount":100000000000000000}\n'
        '{"event":"SUBMITTED_ONCHAIN","job_id":1,"tx":"0xa"}\n'
        '{"event":"SETTLE_DEFERRED","job_id":1,"detail":"0x17be5b7b"}\n'
        '{"event":"CREATE","job_id":2}\n'
        '{"event":"FUND","job_id":2,"amount":100000000000000000}\n'
        '{"event":"SUBMITTED_ONCHAIN","job_id":2,"tx":"0xb"}\n'
        '{"event":"SETTLE","job_id":2,"status":"COMPLETED"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(reads, "DATA_DIR", tmp_path)
    out = reads._commerce_jobs()
    assert out["jobs_served"] == 2 and out["jobs_settled"] == 1
    assert out["jobs_pending_settle"] == 1  # job 1 served + deferred, not settled
    assert out["last_settle_status"] == "settled"  # chronological: job 2's SETTLE is last


def test_settle_endpoint_operator_only_403(client, monkeypatch):
    monkeypatch.setattr(commerce, "buyer_available", lambda: False)
    r = client.post("/api/commerce/settle", json={})
    assert r.status_code == 403 and r.json()["ok"] is False


def test_settle_endpoint_success_shape(client, monkeypatch):
    monkeypatch.setattr(commerce, "buyer_available", lambda: True)
    monkeypatch.setattr(
        commerce, "settle_pending_jobs",
        lambda ids: {"ok": True, "pending_before": 2,
                     "settled": [{"job_id": 7, "status": "COMPLETED"}],
                     "deferred": [{"job_id": 8, "reason": "dispute window still open"}],
                     "errors": [], "network": "bsc-mainnet", "message": "1 settled · 1 deferred"},
    )
    r = client.post("/api/commerce/settle", json={"job_ids": [7, 8]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["pending_before"] == 2
    assert body["settled"][0]["job_id"] == 7 and body["deferred"][0]["job_id"] == 8


def test_create_job_never_500_on_loop_error(client, monkeypatch):
    monkeypatch.setattr(commerce, "buyer_available", lambda: True)

    def _boom(q, **k):
        raise RuntimeError("rpc down")

    monkeypatch.setattr(commerce, "create_and_serve_job", _boom)
    r = client.post("/api/commerce/create-job", json={"description": "x"})
    assert r.status_code == 200  # surfaced as ok:false, never crashes the server
    body = r.json()
    assert body["ok"] is False and "rpc down" in body["message"]
