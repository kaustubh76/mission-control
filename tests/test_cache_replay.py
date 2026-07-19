"""
Tests for ictbot.data.cache + ictbot.data.replay.

No network — synthetic DataFrames written to a tmp_path cache.
"""

import pandas as pd
import pytest

from ictbot.data import cache, replay


@pytest.fixture
def patched_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    return tmp_path


def _df(n: int, start_ms: int = 0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.to_datetime([start_ms + i * 60_000 for i in range(n)], unit="ms"),
            "open": [100 + i for i in range(n)],
            "high": [101 + i for i in range(n)],
            "low": [99 + i for i in range(n)],
            "close": [100 + i for i in range(n)],
            "volume": [10] * n,
        }
    )


def test_write_then_read_roundtrip(patched_cache_dir):
    df = _df(5)
    cache.write("binance", "BTC/USDT:USDT", "1m", df)
    got = cache.read("binance", "BTC/USDT:USDT", "1m")
    assert got is not None
    assert len(got) == 5
    assert list(got.columns) == ["time", "open", "high", "low", "close", "volume"]


def test_read_returns_none_when_missing(patched_cache_dir):
    assert cache.read("binance", "ETH/USDT:USDT", "5m") is None


def test_merge_dedupes_on_time_keeping_freshest(patched_cache_dir):
    first = _df(3)
    cache.write("binance", "BTC/USDT:USDT", "1m", first)

    # Overlap rows 1-2 with NEW close prices, plus a new row at row 3.
    overlap = first.copy().iloc[1:]
    overlap.loc[:, "close"] = [9999, 9998]
    new = _df(1, start_ms=3 * 60_000)
    second = pd.concat([overlap, new], ignore_index=True)
    cache.write("binance", "BTC/USDT:USDT", "1m", second)

    got = cache.read("binance", "BTC/USDT:USDT", "1m")
    # Rows = 4 (no duplicates on time).
    assert len(got) == 4
    # The freshest write wins on overlap.
    assert got.iloc[1]["close"] == 9999
    assert got.iloc[2]["close"] == 9998


def test_replay_exchange_serves_from_cache(patched_cache_dir):
    cache.write("binance", "BTC/USDT:USDT", "1m", _df(10))
    ex = replay.ReplayExchange("binance")
    df = ex.fetch_ohlcv("BTC/USDT:USDT", "1m", limit=3)
    assert len(df) == 3
    assert df.iloc[-1]["close"] == 100 + 9  # tail


def test_replay_miss_raises(patched_cache_dir):
    ex = replay.ReplayExchange("binance")
    with pytest.raises(replay.ReplayMiss):
        ex.fetch_ohlcv("ETH/USDT:USDT", "5m", limit=100)


def test_slug_safe_filesystem_path():
    p = cache.cache_path("binance", "BTC/USDT:USDT", "1m")
    assert "BTC_USDT_USDT" in str(p)
    assert "/" not in p.name
    assert ":" not in p.name
