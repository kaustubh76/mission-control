"""
Unit tests for the hardened CmcClient (rate limiter + credit ledger + retry + cache).

Fully hermetic — no network. The HTTP layer is stubbed via the `client._opener` seam
and the clock via `cmc_client._clock`, so rate-limit waits, budget rollover, retries,
and cache degradation are all driven deterministically.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone

import pytest

from ictbot.data import cmc_client
from ictbot.data.cmc_client import CmcClient, _CreditLedger, _RateLimitStall, _TokenBucket


# --------------------------------------------------------------------------- #
# Helpers — a fake urlopen response + a scriptable opener
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, body: dict, status: int = 200):
        self._b = json.dumps(body).encode()
        self.status = status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(script):
    """script = list of dicts (success body) or Exceptions (raised). The last entry
    repeats. Returns an opener with a `.n` call counter."""
    state = {"n": 0}

    def opener(req, timeout=None):
        i = state["n"]
        state["n"] += 1
        item = script[min(i, len(script) - 1)]
        if isinstance(item, Exception):
            raise item
        return _FakeResp(item)

    opener.state = state
    return opener


def _ok_body(value=42, credit_count=1, error_code=0):
    return {
        "status": {"error_code": error_code, "credit_count": credit_count},
        "data": {"value": value},
    }


def _client(tmp_path, **kw):
    kw.setdefault("api_key", "test-key")
    kw.setdefault("disk_cache", False)
    kw.setdefault("rpm", 6000)  # effectively no rate stall in tests
    kw.setdefault("max_wait_s", 0.05)
    kw.setdefault("state_path", tmp_path / "cmc_usage.json")
    return CmcClient(**kw)


# --------------------------------------------------------------------------- #
# Token bucket
# --------------------------------------------------------------------------- #
def test_token_bucket_allows_burst_then_stalls():
    b = _TokenBucket(60)  # capacity 60, refill 1/s
    for _ in range(60):
        assert b.acquire(0.0) == 0.0  # burst the full capacity with no wait
    with pytest.raises(_RateLimitStall):  # 61st needs ~1s; bounded wait 0 → stall
        b.acquire(0.0)


def test_token_bucket_returns_quickly_when_available():
    b = _TokenBucket(6000)  # 100/s refill — always a token ready
    assert b.acquire(1.0) == 0.0


# --------------------------------------------------------------------------- #
# Credit ledger
# --------------------------------------------------------------------------- #
def test_ledger_records_and_rolls(tmp_path, monkeypatch):
    day1 = datetime(2026, 6, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(cmc_client, "_clock", lambda: day1)
    led = _CreditLedger(tmp_path / "u.json", daily_budget=100, monthly_budget=1000)
    led.record(5, 200)
    led.record(3, 200)
    s = led.snapshot()
    assert s["day_credits"] == 8 and s["month_credits"] == 8 and s["req_count"] == 2

    # New day → day counter rolls, month persists.
    monkeypatch.setattr(cmc_client, "_clock", lambda: datetime(2026, 6, 11, tzinfo=timezone.utc))
    s2 = led.snapshot()
    assert s2["day_credits"] == 0 and s2["month_credits"] == 8

    # New month → both roll.
    monkeypatch.setattr(cmc_client, "_clock", lambda: datetime(2026, 7, 1, tzinfo=timezone.utc))
    s3 = led.snapshot()
    assert s3["day_credits"] == 0 and s3["month_credits"] == 0


def test_ledger_budget_blocks(tmp_path):
    assert _CreditLedger(tmp_path / "u.json", 0, 100).can_spend() is False
    assert _CreditLedger(tmp_path / "u.json", 10, 100).can_spend() is True


def test_ledger_persists_atomically(tmp_path):
    path = tmp_path / "u.json"
    _CreditLedger(path, 100, 1000).record(7, 200)
    # A fresh ledger reloads the persisted state (atomic tmp+replace already flushed).
    assert _CreditLedger(path, 100, 1000).snapshot()["day_credits"] == 7
    assert json.loads(path.read_text())["day_credits"] == 7


# --------------------------------------------------------------------------- #
# get() — success, credit accounting, error handling
# --------------------------------------------------------------------------- #
def test_get_success_records_credits_and_caches(tmp_path):
    c = _client(tmp_path)
    c._opener = _opener([_ok_body(value=99, credit_count=2)])
    body = c.get("/v1/x", {"a": 1}, cache_ttl=1000)
    assert body["data"]["value"] == 99
    assert c.telemetry()["credits_today"] == 2  # parsed status.credit_count
    # Second call within TTL → served from cache, no second network hit.
    assert c.get("/v1/x", {"a": 1}, cache_ttl=1000)["data"]["value"] == 99
    assert c._opener.state["n"] == 1


def test_get_string_error_code_zero_is_success(tmp_path):
    """Regression: CMC /v3 returns error_code as the STRING "0" on success; a naive
    truthiness check treats it as an error. Must be parsed as success."""
    c = _client(tmp_path)
    c._opener = _opener([_ok_body(value=16, error_code="0")])
    assert c.get("/v3/fear-and-greed/latest", {}, cache_ttl=10)["data"]["value"] == 16


def test_get_nonzero_error_code_returns_none(tmp_path):
    c = _client(tmp_path)
    c._opener = _opener([_ok_body(error_code="1006")])  # plan/endpoint error at HTTP 200
    assert c.get("/v1/gated", {}, cache_ttl=10) is None


# --------------------------------------------------------------------------- #
# get() — budget + rate-limit degradation (never block / never overspend)
# --------------------------------------------------------------------------- #
def test_get_budget_exhausted_never_calls_network(tmp_path):
    c = _client(tmp_path, daily_budget=0)

    def boom(req, timeout=None):
        raise AssertionError("network must not be called when budget is exhausted")

    c._opener = boom
    assert c.get("/v1/x", {}, cache_ttl=10) is None  # no cache, no network → None


def test_get_degrades_to_stale_cache_on_exhaustion(tmp_path):
    # Budget of 1: the priming call spends it, the next call must serve stale cache.
    c = _client(tmp_path, daily_budget=1)
    c._opener = _opener([_ok_body(value=7)])
    assert c.get("/v1/x", {}, cache_ttl=1000)["data"]["value"] == 7  # primes + spends 1
    # force=True bypasses the fresh-cache shortcut, but budget is now exhausted →
    # degrade to the cached body instead of hitting the network again.
    assert c.get("/v1/x", {}, cache_ttl=1000, force=True)["data"]["value"] == 7
    assert c._opener.state["n"] == 1


def test_get_rate_stall_degrades_to_cache(tmp_path):
    c = _client(tmp_path, rpm=60, max_wait_s=0.0)
    c._opener = _opener([_ok_body(value=5)])
    assert c.get("/v1/x", {}, cache_ttl=1000)["data"]["value"] == 5  # primes cache
    # Drain the bucket so the next acquire would stall; bounded wait 0 → degrade.
    for _ in range(60):
        try:
            c.bucket.acquire(0.0)
        except _RateLimitStall:
            break
    assert c.get("/v1/x", {}, cache_ttl=0, force=True)["data"]["value"] == 5
    assert c._opener.state["n"] == 1  # no extra network


# --------------------------------------------------------------------------- #
# get() — retry/backoff
# --------------------------------------------------------------------------- #
def test_get_retries_on_429_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(cmc_client.time, "sleep", lambda *_: None)  # no real backoff sleep
    c = _client(tmp_path, max_retries=2, max_wait_s=0.01)
    err = urllib.error.HTTPError("http://x", 429, "rate limited", None, None)
    c._opener = _opener([err, _ok_body(value=123)])
    assert c.get("/v1/x", {}, cache_ttl=10)["data"]["value"] == 123
    assert c._opener.state["n"] == 2  # one retry, then success
