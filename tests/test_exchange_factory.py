"""
Exchange + broker factory tests. Verify EXCHANGE=delta|binance picks the
right adapter and broker without importing the unwanted module path
into either test.

Tests mutate settings.exchange via monkeypatch, then reset the lazy
singleton in data.factory so the next get_default_exchange() call
re-resolves from the new setting.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ictbot.data import factory as data_factory
from ictbot.exec import factory as exec_factory
from ictbot.settings import settings


@pytest.fixture(autouse=True)
def _reset_data_factory_singleton():
    """Each test starts with no cached default exchange."""
    data_factory.reset_default_exchange()
    yield
    data_factory.reset_default_exchange()


# ---- data factory ---------------------------------------------------------


def test_data_factory_picks_delta_by_default(monkeypatch):
    monkeypatch.setattr(settings, "exchange", "delta")
    # Patch the ccxt constructor so we don't make a real network call.
    import ictbot.data.delta as delta_mod

    monkeypatch.setattr(delta_mod.ccxt, "delta", lambda opts=None: MagicMock())
    ex = data_factory.get_default_exchange()
    assert ex.name == "delta"


def test_data_factory_unknown_exchange_raises(monkeypatch):
    # Use a value that's NOT in the supported set (delta / binance) so
    # the factory's "unknown" branch fires. "kraken" is intentionally
    # never added to keep this test future-proof.
    monkeypatch.setattr(settings, "exchange", "kraken")
    with pytest.raises(ValueError, match="Unknown EXCHANGE"):
        data_factory.get_default_exchange()


def test_data_factory_get_data_delegates_to_singleton(monkeypatch):
    fake = MagicMock()
    fake.name = "fake"
    fake.fetch_ohlcv.return_value = "df-sentinel"
    data_factory.set_default_exchange(fake)

    result = data_factory.get_data("BTC/USDT:USDT", "1m", 100)
    fake.fetch_ohlcv.assert_called_once_with("BTC/USDT:USDT", "1m", 100)
    assert result == "df-sentinel"


def test_data_factory_singleton_is_cached(monkeypatch):
    monkeypatch.setattr(settings, "exchange", "delta")
    import ictbot.data.delta as delta_mod

    monkeypatch.setattr(delta_mod.ccxt, "delta", lambda opts=None: MagicMock())

    a = data_factory.get_default_exchange()
    b = data_factory.get_default_exchange()
    assert a is b


# ---- exec factory ---------------------------------------------------------


def test_exec_factory_picks_delta_live_broker(monkeypatch):
    monkeypatch.setattr(settings, "exchange", "delta")
    monkeypatch.setattr(settings, "enable_live_trading", True)
    import ictbot.exec.delta_live as delta_live_mod

    monkeypatch.setattr(delta_live_mod.ccxt, "delta", lambda opts=None: MagicMock())

    broker = exec_factory.build_live_broker(allowed_pairs={"BTC/USDT:USDT"})
    assert broker.name == "delta-live"
    assert broker.allowed_pairs == {"BTC/USDT:USDT"}


def test_exec_factory_unknown_exchange_raises(monkeypatch):
    # Use a value that's NOT in the supported set (delta / binance) so
    # the factory's "unknown" branch fires. "kraken" is intentionally
    # never added to keep this test future-proof.
    monkeypatch.setattr(settings, "exchange", "kraken")
    with pytest.raises(ValueError, match="Unknown EXCHANGE"):
        exec_factory.build_live_broker()
