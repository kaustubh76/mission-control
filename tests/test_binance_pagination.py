"""
Tests for ictbot.data.binance.BinanceExchange.fetch_ohlcv pagination.

Specific regression for the silent-cap bug discovered 2026-06-05: binance
Futures /fapi/v1/klines hard-caps at 1000 bars per call. The old code set
PAGE_SIZE=1500 and used `if len(page) < page_limit: break` to terminate,
so every request silently came back as 1000 < 1500 and the loop stopped
after one iteration. WFO at --bars 20000 received only 1000 bars instead
of 20000, making walk-forward validation impossible.
"""

from ictbot.data.binance import PAGE_SIZE, BinanceExchange


class _FakeClient:
    """Stand-in for ccxt.binance that mimics the silent-cap behaviour."""

    def __init__(self, total_bars: int, hard_cap: int = PAGE_SIZE):
        self.total_bars = total_bars
        self.hard_cap = hard_cap  # the exchange's actual per-call limit
        self.calls: list[dict] = []
        self.now_ms = 10_000_000

    def milliseconds(self):
        return self.now_ms

    def fetch_ohlcv(self, symbol, timeframe, limit=300, since=None):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "limit": limit, "since": since}
        )
        # The exchange ignores `limit` above its hard cap and returns at
        # most hard_cap bars. This is the bug-reproduction shape.
        effective_limit = min(limit, self.hard_cap)
        anchor = since if since is not None else self.now_ms - effective_limit * 60_000
        rows = []
        for i in range(effective_limit):
            ts = anchor + i * 60_000
            if ts >= self.now_ms:
                break
            rows.append([ts, 100.0, 101.0, 99.0, 100.5, 10])
        return rows[-min(self.total_bars, len(rows)) :]


def test_single_page_when_limit_under_page_size():
    ex = BinanceExchange()
    fake = _FakeClient(total_bars=500)
    ex._client = fake
    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=300)
    assert len(df) <= 300
    assert len(fake.calls) == 1
    assert fake.calls[0]["limit"] == 300
    assert fake.calls[0]["since"] is None  # legacy fast-path


def test_pagination_accumulates_multiple_pages():
    """Regression: ask for 3.5x the page size, must get all of it."""
    ex = BinanceExchange()
    fake = _FakeClient(total_bars=PAGE_SIZE * 5)
    ex._client = fake
    target = PAGE_SIZE * 3 + 500  # 3500 with PAGE_SIZE=1000
    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=target)
    assert len(df) == target, f"expected {target} bars, got {len(df)}"
    # Should have made enough paginated calls — at least 4 for 3500/1000.
    paginated = [c for c in fake.calls if c["since"] is not None]
    assert len(paginated) >= 4


def test_silent_cap_does_not_truncate_loop():
    """The original bug: silent cap (returns less than asked) used to fire
    the `if len(page) < page_limit: break` early-termination after page 1.
    With PAGE_SIZE matched to the real hard cap AND the short-page break
    removed, we still get the full requested limit."""
    ex = BinanceExchange()
    # Simulate exchange that caps at PAGE_SIZE (1000) but the test asks
    # for 4x that with a synthetic universe big enough to satisfy it.
    fake = _FakeClient(total_bars=PAGE_SIZE * 10, hard_cap=PAGE_SIZE)
    ex._client = fake
    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=PAGE_SIZE * 4)
    assert len(df) == PAGE_SIZE * 4


def test_paginated_result_dedupes_on_time():
    ex = BinanceExchange()
    fake = _FakeClient(total_bars=PAGE_SIZE * 3)
    ex._client = fake
    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=PAGE_SIZE * 2)
    assert df["time"].is_unique


def test_empty_page_terminates_loop():
    """When the exchange has run out of older history, it returns []. The
    loop must break and return what we have so far."""
    ex = BinanceExchange()

    class _HistoricalFloor:
        """Returns PAGE_SIZE bars for the first 2 calls, then empty —
        simulating a historical horizon hit."""

        calls = 0
        now_ms = 10_000_000

        def milliseconds(self):
            return self.now_ms

        def fetch_ohlcv(self, symbol, timeframe, limit=300, since=None):
            self.calls += 1
            if self.calls > 2:
                return []
            anchor = since if since is not None else self.now_ms - limit * 60_000
            return [[anchor + i * 60_000, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(limit)]

    fake = _HistoricalFloor()
    ex._client = fake
    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=PAGE_SIZE * 5)
    # Got 2 pages worth, then stopped on empty. Loop didn't loop forever.
    assert len(df) == PAGE_SIZE * 2
    assert fake.calls == 3  # 2 productive + 1 empty


def test_unknown_timeframe_falls_back_to_single_page():
    ex = BinanceExchange()
    fake = _FakeClient(total_bars=1000)
    ex._client = fake
    ex.fetch_ohlcv("BTC/USDT:USDT", "weird-tf", limit=PAGE_SIZE * 2)
    assert len(fake.calls) == 1
    assert fake.calls[0]["limit"] == PAGE_SIZE


def test_max_iters_bounds_runaway_loop():
    """If the exchange returned 0 bars per call AND somehow the empty-break
    didn't fire, max_iters must stop us. Use a fake that returns one bar
    per call to force many iterations."""
    ex = BinanceExchange()

    class _OneAtATime:
        calls = 0
        now_ms = 10_000_000

        def milliseconds(self):
            return self.now_ms

        def fetch_ohlcv(self, *a, **kw):
            self.calls += 1
            # Each call yields one bar one minute back from `since`.
            since = kw.get("since") or (self.now_ms - 60_000)
            return [[since, 1.0, 1.0, 1.0, 1.0, 1.0]]

    fake = _OneAtATime()
    ex._client = fake
    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=PAGE_SIZE * 3)
    # With max_iters = limit // PAGE_SIZE + 4 = 7, the loop must stop
    # well before exhausting a 3000-iter walk.
    assert fake.calls <= 10, f"runaway: {fake.calls} calls"
