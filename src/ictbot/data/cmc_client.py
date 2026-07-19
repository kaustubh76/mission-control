"""
Hardened CoinMarketCap API client — the single seam ALL CMC HTTP routes through.

The CMC Startup (commercial) plan gives 300k credits/mo, ~10k/day, 30 req/min, 28
endpoints. Hitting that surface naively (no rate-limit, no credit accounting) would
either stall on 429s or silently blow the monthly cap. This module is the commercial-
grade foundation that makes heavy CMC use SAFE:

  - **Token-bucket rate limiter** (30 rpm). `acquire()` is BOUNDED by `cmc_max_wait_s`
    — past that it raises `_RateLimitStall` and the caller degrades to cache and returns
    immediately, so a request path (e.g. the 4s dashboard poll) never blocks for long.
  - **Credit-budget ledger** persisted to data/journal/cmc_usage.json (atomic
    tmp+os.replace, mirroring run_allocator.save_state). Parses the REAL cost from each
    response's `status.credit_count`, rolls day/month counters, and ENFORCES soft
    budgets BEFORE issuing a request (returns cache/None when exhausted).
  - **Retry/backoff** with jitter on 429/5xx, honoring `Retry-After`; fail-fast on 4xx.
  - **Per-endpoint TTL cache** (in-proc + optional on-disk under data/cache/cmc/) so a
    fresh process / dashboard restart still has the last-good payload to degrade to.
  - **Telemetry** (`telemetry()`) for the dashboard "CMC API" health card.

The public contract matches what the existing callers already rely on: `get()` NEVER
raises — on any failure (budget exhausted, rate stall, HTTP error, parse error) it
returns the freshest cache entry (even if stale) or None. `data/ictbot/cmc.py` wraps
this with its existing `cmc_price` / `fear_greed` signatures unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ictbot.settings import CACHE_DIR, JOURNAL_DIR, settings

CMC_BASE = "https://pro-api.coinmarketcap.com"

# Default cache TTL (seconds) by data class — callers may override per call.
_TTL_BY_CLASS = {
    "quotes": 60,
    "global_metrics": 300,
    "daily_ohlcv": 6 * 3600,
    "categories": 1800,
    "trending": 1800,
    "listings": 600,
    "fear_greed": 3600,
    "generic": 120,
}

# Injectable clock so tests can drive day/month rollover deterministically.
_clock = lambda: datetime.now(timezone.utc)  # noqa: E731


def _today() -> str:
    return _clock().strftime("%Y-%m-%d")


def _month() -> str:
    return _clock().strftime("%Y-%m")


class _RateLimitStall(Exception):
    """Raised when waiting for a rate-limit token would exceed the bounded budget."""


# --------------------------------------------------------------------------- #
# Token-bucket rate limiter (thread-safe)
# --------------------------------------------------------------------------- #
class _TokenBucket:
    def __init__(self, rpm: int):
        self.capacity = float(max(1, rpm))
        self.refill_per_s = max(1, rpm) / 60.0
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, max_wait_s: float) -> float:
        """Consume one token, blocking until one frees. Returns seconds waited.
        Raises _RateLimitStall if the wait would exceed `max_wait_s`."""
        deadline = time.monotonic() + max(0.0, max_wait_s)
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.updated) * self.refill_per_s
                )
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return waited
                need = (1.0 - self.tokens) / self.refill_per_s
            if time.monotonic() + need > deadline:
                raise _RateLimitStall(need)
            sleep_for = min(need, max(0.0, deadline - time.monotonic()))
            if sleep_for <= 0:
                raise _RateLimitStall(need)
            time.sleep(sleep_for)
            waited += sleep_for


# --------------------------------------------------------------------------- #
# Credit-budget ledger (persisted, atomic)
# --------------------------------------------------------------------------- #
class _CreditLedger:
    """Persisted credit-budget ledger. Writes are ATOMIC (tmp + os.replace) and
    THREAD-SAFE (a process-local Lock), but NOT cross-process (CMC-2): two separate
    processes writing concurrently could under-count. Safe in practice — a single cron
    writer, ~12x credit headroom, and CMC enforces the real hard cap server-side."""

    def __init__(self, path: Path, daily_budget: int, monthly_budget: int):
        self.path = path
        self.daily_budget = int(daily_budget)
        self.monthly_budget = int(monthly_budget)
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                pass
        return {
            "month": _month(),
            "month_credits": 0,
            "day": _today(),
            "day_credits": 0,
            "req_count": 0,
            "last_status": None,
            "last_credit_count": 0,
            "rate_wait_total_s": 0.0,
        }

    def _roll(self) -> None:
        d, m = _today(), _month()
        if self._state.get("day") != d:
            self._state["day"], self._state["day_credits"] = d, 0
        if self._state.get("month") != m:
            self._state["month"], self._state["month_credits"] = m, 0

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._state, indent=2))
            os.replace(tmp, self.path)
        except Exception:
            pass

    def can_spend(self) -> bool:
        with self._lock:
            self._roll()
            return (
                self._state["day_credits"] < self.daily_budget
                and self._state["month_credits"] < self.monthly_budget
            )

    def record(self, credits: int, status) -> None:
        with self._lock:
            self._roll()
            c = int(credits)
            self._state["day_credits"] += c
            self._state["month_credits"] += c
            self._state["req_count"] += 1
            self._state["last_status"] = status
            self._state["last_credit_count"] = c
            self._save()

    def add_wait(self, secs: float) -> None:
        with self._lock:
            self._state["rate_wait_total_s"] = round(
                float(self._state.get("rate_wait_total_s", 0.0)) + secs, 2
            )
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            self._roll()
            return dict(self._state)


# --------------------------------------------------------------------------- #
# The client
# --------------------------------------------------------------------------- #
class CmcClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        rpm: int | None = None,
        daily_budget: int | None = None,
        monthly_budget: int | None = None,
        max_retries: int | None = None,
        max_wait_s: float | None = None,
        disk_cache: bool | None = None,
        state_path: Path | None = None,
        base: str = CMC_BASE,
        timeout: float = 15.0,
    ):
        self._explicit_key = api_key
        self.rpm = int(rpm if rpm is not None else settings.cmc_rate_limit_rpm)
        self.daily_budget = int(
            daily_budget if daily_budget is not None else settings.cmc_daily_credit_budget
        )
        self.monthly_budget = int(
            monthly_budget if monthly_budget is not None else settings.cmc_monthly_credit_budget
        )
        self.max_retries = int(max_retries if max_retries is not None else settings.cmc_max_retries)
        self.max_wait_s = float(max_wait_s if max_wait_s is not None else settings.cmc_max_wait_s)
        self.disk_cache = bool(disk_cache if disk_cache is not None else settings.cmc_disk_cache)
        self.base = base
        self.timeout = timeout
        self.bucket = _TokenBucket(self.rpm)
        self.ledger = _CreditLedger(
            state_path or (JOURNAL_DIR / "cmc_usage.json"), self.daily_budget, self.monthly_budget
        )
        self._cache: dict = {}  # key -> (wall_ts, payload)
        self._disk_dir = CACHE_DIR / "cmc"
        # Seam for tests: replace to stub the network without monkeypatching urllib.
        self._opener = urllib.request.urlopen

    # ---- key resolution (explicit -> env -> settings) ----
    def _key(self, override: str | None = None) -> str:
        if override:
            return override
        if self._explicit_key:
            return self._explicit_key
        env = os.environ.get("CMC_API_KEY", "")
        if env:
            return env
        try:
            return settings.cmc_api_key or ""
        except Exception:
            return ""

    # ---- disk cache helpers ----
    def _disk_path(self, path: str, params: dict) -> Path:
        raw = path + "?" + urllib.parse.urlencode(sorted(params.items()))
        return self._disk_dir / (hashlib.sha1(raw.encode()).hexdigest() + ".json")

    def _disk_read(self, path: str, params: dict) -> tuple[float, dict] | None:
        if not self.disk_cache:
            return None
        p = self._disk_path(path, params)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text())
            return float(blob["ts"]), blob["payload"]
        except Exception:
            return None

    def _disk_write(self, path: str, params: dict, payload: dict) -> None:
        if not self.disk_cache:
            return
        try:
            self._disk_dir.mkdir(parents=True, exist_ok=True)
            p = self._disk_path(path, params)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"ts": time.time(), "payload": payload}))
            os.replace(tmp, p)
        except Exception:
            pass

    # ---- retry/backoff HTTP ----
    def _backoff(self, attempt: int, retry_after) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.max_wait_s)
            except (TypeError, ValueError):
                pass
        return min(0.5 * (2**attempt) + random.uniform(0.0, 0.4), self.max_wait_s)

    def _request(self, url: str, headers: dict):
        """Return (status_code, body_dict|None). Retries 429/5xx + connection errors;
        raises only if connection errors persist past max_retries."""
        attempt = 0
        while True:
            try:
                req = urllib.request.Request(url, headers=headers)
                with self._opener(req, timeout=self.timeout) as resp:
                    return getattr(resp, "status", 200), json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                code = e.code
                if code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    time.sleep(self._backoff(attempt, ra))
                    attempt += 1
                    continue
                try:
                    return code, json.loads(e.read().decode())
                except Exception:
                    return code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt < self.max_retries:
                    time.sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise

    # ---- the one entrypoint ----
    def get(
        self,
        path: str,
        params: dict | None = None,
        *,
        cache_ttl: float | None = None,
        est_credits: int = 1,
        data_class: str = "generic",
        degrade_to_cache: bool = True,
        force: bool = False,
        max_wait_s: float | None = None,
        api_key: str | None = None,
    ) -> dict | None:
        """Fetch one CMC endpoint. NEVER raises — returns the JSON body, the freshest
        cache entry on any failure, or None. Accounts credits + rate-limits transparently."""
        params = params or {}
        ttl = cache_ttl if cache_ttl is not None else _TTL_BY_CLASS.get(data_class, 120)
        key = (path, tuple(sorted((k, str(v)) for k, v in params.items())))
        now = time.time()

        # 1. fresh in-proc cache hit
        hit = self._cache.get(key)
        if hit and not force and (now - hit[0]) < ttl:
            return hit[1]
        # 1b. seed from disk on a cold process
        if hit is None:
            disk = self._disk_read(path, params)
            if disk is not None:
                self._cache[key] = (disk[0], disk[1])
                hit = self._cache[key]
                if not force and (now - disk[0]) < ttl:
                    return disk[1]

        # 2. need the network — require a key
        api_key = self._key(api_key)
        if not api_key:
            return hit[1] if hit else None

        # 3. budget enforcement (pre-request)
        if not self.ledger.can_spend():
            return hit[1] if (hit and degrade_to_cache) else None

        # 4. rate limit (bounded — degrade rather than block forever)
        try:
            waited = self.bucket.acquire(max_wait_s if max_wait_s is not None else self.max_wait_s)
            if waited:
                self.ledger.add_wait(waited)
        except _RateLimitStall:
            return hit[1] if (hit and degrade_to_cache) else None

        # 5. issue the request
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"X-CMC_PRO_API_KEY": api_key, "Accept": "application/json"}
        try:
            status_code, body = self._request(url, headers)
        except Exception:
            return hit[1] if (hit and degrade_to_cache) else None

        # 6. account the real credit cost (status.credit_count), then evaluate success
        credits = est_credits
        if isinstance(body, dict):
            cc = (body.get("status") or {}).get("credit_count")
            if cc is not None:
                try:
                    credits = int(cc)
                except (TypeError, ValueError):
                    pass
        self.ledger.record(credits, status_code)

        # CMC returns error_code as int 0 (v1/v2) OR string "0" (v3) on success —
        # normalize before deciding (a non-empty string like "0" is TRUTHY in Python).
        err_raw = (body.get("status") or {}).get("error_code") if isinstance(body, dict) else 1
        try:
            err_code = int(err_raw) if err_raw not in (None, "") else 0
        except (TypeError, ValueError):
            err_code = 1
        if status_code == 200 and isinstance(body, dict) and err_code == 0:
            self._cache[key] = (now, body)
            self._disk_write(path, params, body)
            return body
        # 7. failure → stale cache (any age) as graceful degradation
        return hit[1] if (hit and degrade_to_cache) else None

    # ---- introspection (dashboard "CMC API" card) ----
    def telemetry(self) -> dict:
        s = self.ledger.snapshot()
        return {
            "credits_today": s["day_credits"],
            "daily_budget": self.daily_budget,
            "credits_month": s["month_credits"],
            "monthly_budget": self.monthly_budget,
            "req_count": s["req_count"],
            "last_status": s["last_status"],
            "last_credit_count": s["last_credit_count"],
            "rate_wait_total_s": s["rate_wait_total_s"],
            "rpm": self.rpm,
            "near_cap_day": s["day_credits"] >= 0.8 * self.daily_budget,
            "near_cap_month": s["month_credits"] >= 0.8 * self.monthly_budget,
            "healthy": s["last_status"] in (None, 200),
            "key_set": bool(self._key()),
        }

    def budget_remaining(self) -> tuple[int, int]:
        s = self.ledger.snapshot()
        return (
            max(0, self.daily_budget - s["day_credits"]),
            max(0, self.monthly_budget - s["month_credits"]),
        )

    def healthy(self) -> bool:
        return self.telemetry()["healthy"]


# Module-level singleton — every CMC call in the codebase routes through this.
CMC = CmcClient()
