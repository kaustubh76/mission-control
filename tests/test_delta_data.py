"""
DeltaExchange data-adapter tests. Every test mocks the ccxt client —
nothing here hits api.delta.exchange.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ictbot.data.delta import DeltaExchange


def _ohlcv_row(ts_ms: int, c: float) -> list:
    """Single OHLCV row in ccxt shape: [time, open, high, low, close, volume]."""
    return [ts_ms, c - 1, c + 1, c - 2, c, 100.0]


def test_fetch_ohlcv_under_page_size_makes_one_call():
    """Delta paginates backwards from `now`. A single page that covers
    the request returns after one call. We always pass `since` (Delta
    rejects bare `limit` calls)."""
    client = MagicMock()
    # `now` in ms — picked so the test is timestamp-agnostic.
    client.milliseconds.return_value = 2000 * 60_000
    # 50 ascending 1m bars; the page is shorter than PAGE_SIZE so the
    # backward walk terminates after one fetch.
    client.fetch_ohlcv.return_value = [_ohlcv_row((1000 + i) * 60_000, 100 + i) for i in range(50)]
    ex = DeltaExchange(client=client)

    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=50)

    assert client.fetch_ohlcv.call_count == 1
    _, kwargs = client.fetch_ohlcv.call_args
    assert kwargs["limit"] == 1000  # PAGE_SIZE
    assert "since" in kwargs and isinstance(kwargs["since"], int)
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 50
    assert df["time"].dtype.kind == "M"  # datetime64


def test_fetch_ohlcv_paginates_backwards_when_limit_exceeds_page():
    """1500 bars on 1m → ccxt called at least twice walking backwards."""
    client = MagicMock()
    # First call returns the newest 1000 bars; the loop steps the window
    # back and fetches the older 500-bar page.
    first_page = [_ohlcv_row((500 + i) * 60_000, 200 + i) for i in range(1000)]
    second_page = [_ohlcv_row(i * 60_000, 100 + i) for i in range(500)]
    client.milliseconds.return_value = 1500 * 60_000
    # Third call returns empty so the loop exits cleanly.
    client.fetch_ohlcv.side_effect = [first_page, second_page, []]

    ex = DeltaExchange(client=client)
    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=1500)

    assert client.fetch_ohlcv.call_count >= 2
    # Should be sorted ascending after page reversal + dedup.
    assert df["time"].is_monotonic_increasing
    assert len(df) <= 1500


def test_tick_size_reads_precision_price():
    client = MagicMock()
    client.load_markets.return_value = {
        "BTC/USDT:USDT": {"precision": {"price": 0.5, "amount": 1.0}, "contractSize": 0.001},
        "XRP/USDT:USDT": {"precision": {"price": 0.0001, "amount": 1.0}, "contractSize": 1.0},
    }
    ex = DeltaExchange(client=client)

    assert ex.tick_size("BTC/USDT:USDT") == 0.5
    assert ex.tick_size("XRP/USDT:USDT") == 0.0001
    # Cached — second call shouldn't re-load.
    ex.tick_size("BTC/USDT:USDT")
    assert client.load_markets.call_count == 1


def test_tick_size_missing_market_returns_none():
    client = MagicMock()
    client.load_markets.return_value = {}
    ex = DeltaExchange(client=client)
    assert ex.tick_size("UNKNOWN/USDT:USDT") is None


def test_contract_size_returns_market_value_or_default():
    client = MagicMock()
    client.load_markets.return_value = {
        "BTC/USDT:USDT": {"precision": {"price": 0.5, "amount": 1.0}, "contractSize": 0.001},
        "SOL/USDT:USDT": {"precision": {"price": 0.0001, "amount": 1.0}, "contractSize": 1.0},
    }
    ex = DeltaExchange(client=client)
    assert ex.contract_size("BTC/USDT:USDT") == 0.001
    assert ex.contract_size("SOL/USDT:USDT") == 1.0
    # Missing market defaults to 1.0 (the safe coin=contract assumption).
    assert ex.contract_size("UNKNOWN/USDT:USDT") == 1.0


def test_qty_step_returns_precision_amount():
    client = MagicMock()
    client.load_markets.return_value = {
        "BTC/USDT:USDT": {"precision": {"price": 0.5, "amount": 1.0}, "contractSize": 0.001},
    }
    ex = DeltaExchange(client=client)
    assert ex.qty_step("BTC/USDT:USDT") == 1.0


def test_rate_limit_triggers_single_retry():
    import ccxt

    client = MagicMock()
    client.milliseconds.return_value = 1000 * 60_000
    client.fetch_ohlcv.side_effect = [
        ccxt.RateLimitExceeded("throttled"),
        [_ohlcv_row(900 * 60_000, 100)],
    ]
    ex = DeltaExchange(retry_cooldown=0, client=client)

    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=1)
    assert client.fetch_ohlcv.call_count == 2  # one retry
    assert len(df) == 1


def test_non_rate_limit_error_does_not_retry():
    client = MagicMock()
    client.milliseconds.return_value = 1000 * 60_000
    client.fetch_ohlcv.side_effect = ValueError("bad symbol")
    ex = DeltaExchange(retry_cooldown=0, client=client)

    import pytest

    with pytest.raises(ValueError, match="bad symbol"):
        ex.fetch_ohlcv("BAD/USDT:USDT", "1m", limit=1)
    assert client.fetch_ohlcv.call_count == 1


def test_fetch_cvd_sums_signed_aggressor_volume():
    client = MagicMock()
    client.fetch_trades.side_effect = [
        [
            {"timestamp": 0, "side": "buy", "amount": 1.0},
            {"timestamp": 100, "side": "sell", "amount": 0.5},
            {"timestamp": 200, "side": "buy", "amount": 2.0},
        ],
        [],  # empty page = stop
    ]
    ex = DeltaExchange(client=client)

    cvd = ex.fetch_cvd("BTC/USDT:USDT", since_ms=0, until_ms=1000)
    assert cvd == 1.0 - 0.5 + 2.0  # = 2.5


def test_fetch_cvd_window_excludes_post_until_trades():
    client = MagicMock()
    client.fetch_trades.return_value = [
        {"timestamp": 0, "side": "buy", "amount": 1.0},
        {"timestamp": 999_999, "side": "buy", "amount": 100.0},  # past until_ms
    ]
    ex = DeltaExchange(client=client)
    cvd = ex.fetch_cvd("BTC/USDT:USDT", since_ms=0, until_ms=500)
    # Only the first trade counts.
    assert cvd == 1.0
