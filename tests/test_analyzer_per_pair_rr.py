"""
Tests for Fix 9.A — analyzer.py wiring of per-pair SL/TP.

The previous behaviour read the module-level `SL_FRAC` / `TP_FRAC` once
at import time, so every pair traded with the same fractions regardless
of its volatility regime. After Fix 9.A, `analyze_pair("SOL/USDT:USDT")`
must read `settings.get_sl_frac("SOL/USDT:USDT")` (with per-pair env
fallback to the global default).

We assert that by mocking the strategy constructor and the data fetcher
so we can intercept what `sl_frac` / `tp_frac` get passed to the
strategy on a real `analyze_pair` call.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ictbot.orchestrator import analyzer
from ictbot.settings import settings


@pytest.fixture(autouse=True)
def _reset_caches():
    """Strategy cache is keyed by knob tuples — must reset between tests
    so each test sees a fresh constructor call."""
    analyzer._reset_strategy_cache()
    yield
    analyzer._reset_strategy_cache()


@pytest.fixture
def _mock_data_and_strategy(monkeypatch):
    """Return a recorder that captures the kwargs `analyze_pair` passes
    to the strategy constructor. The data fetcher is mocked to return a
    minimal DataFrame so `analyze_pair` reaches the strategy without I/O.
    """
    captured: dict[str, dict] = {}

    # Minimal OHLCV DataFrame that's wide enough to pass any MIN_BARS check.
    df = pd.DataFrame(
        {
            "open": [100.0] * 300,
            "high": [101.0] * 300,
            "low": [99.0] * 300,
            "close": [100.5] * 300,
            "volume": [1000.0] * 300,
        },
        index=pd.date_range("2024-01-01", periods=300, freq="1min", tz="UTC"),
    )

    def fake_get_data(*_a, **_kw):
        return df.copy()

    monkeypatch.setattr(analyzer, "get_data", fake_get_data)

    # Intercept the strategy constructor — record its kwargs and short-circuit
    # evaluation by returning a result dict shaped like the real strategy's
    # output (error key + a few defaults the downstream settlement loop reads).
    class _StubStrategy:
        def __init__(self, **kw):
            captured["last"] = kw

        def evaluate(self, *_a, **kw):
            return {
                "pair": kw.get("pair", "?"),
                "error": None,
                "signal": None,
                "entry": None,
                "sl": None,
                "tp": None,
                "confidence": 0,
                "diagnostics": {},
            }

    monkeypatch.setattr(analyzer, "ICTProMaxStrategy", _StubStrategy)
    # The cache builds via _get_or_build_strategy → ICTProMaxStrategy.
    # Reset again now that the constructor is patched.
    analyzer._reset_strategy_cache()
    # Disable TG + journal side effects so the test stays pure.
    monkeypatch.setattr(analyzer, "send_telegram", lambda *_a, **_kw: None)
    monkeypatch.setattr(analyzer, "append_signal", lambda *_a, **_kw: None)
    monkeypatch.setattr(analyzer, "settle_open_signals", lambda *_a, **_kw: None)
    monkeypatch.setattr(analyzer, "load_last_signal", lambda *_a, **_kw: None)
    monkeypatch.setattr(analyzer, "save_last_signal", lambda *_a, **_kw: None)

    return captured


class TestAnalyzerPerPairRR:
    def test_btc_falls_back_to_global_when_no_override(self, _mock_data_and_strategy, monkeypatch):
        monkeypatch.setattr(settings, "sl_frac", 0.005)
        monkeypatch.setattr(settings, "tp_frac", 0.025)
        monkeypatch.setattr(settings, "sl_frac_btc", None)
        monkeypatch.setattr(settings, "tp_frac_btc", None)

        analyzer.analyze_pair("BTC/USDT:USDT", notify=False)

        kw = _mock_data_and_strategy["last"]
        assert kw["sl_frac"] == 0.005
        assert kw["tp_frac"] == 0.025

    def test_sol_uses_per_pair_override(self, _mock_data_and_strategy, monkeypatch):
        # SOL's daily ATR is ~4-5 %; 0.5 % SL is noise. WFO-derived
        # override widens it.
        monkeypatch.setattr(settings, "sl_frac", 0.005)
        monkeypatch.setattr(settings, "tp_frac", 0.025)
        monkeypatch.setattr(settings, "sl_frac_sol", 0.012)
        monkeypatch.setattr(settings, "tp_frac_sol", 0.040)

        analyzer.analyze_pair("SOL/USDT:USDT", notify=False)

        kw = _mock_data_and_strategy["last"]
        assert kw["sl_frac"] == 0.012
        assert kw["tp_frac"] == 0.040

    def test_each_pair_reads_its_own_value(self, _mock_data_and_strategy, monkeypatch):
        """A single scan over the 4 configured pairs must produce 4
        distinct (sl_frac, tp_frac) tuples when all 4 overrides are
        set."""
        monkeypatch.setattr(settings, "sl_frac", 0.005)
        monkeypatch.setattr(settings, "tp_frac", 0.025)
        monkeypatch.setattr(settings, "sl_frac_btc", 0.004)
        monkeypatch.setattr(settings, "sl_frac_eth", 0.006)
        monkeypatch.setattr(settings, "sl_frac_sol", 0.012)
        monkeypatch.setattr(settings, "sl_frac_xrp", 0.010)
        monkeypatch.setattr(settings, "tp_frac_btc", 0.020)
        monkeypatch.setattr(settings, "tp_frac_eth", 0.030)
        monkeypatch.setattr(settings, "tp_frac_sol", 0.040)
        monkeypatch.setattr(settings, "tp_frac_xrp", 0.050)

        seen = {}
        for pair in [
            "BTC/USDT:USDT",
            "ETH/USDT:USDT",
            "SOL/USDT:USDT",
            "XRP/USDT:USDT",
        ]:
            analyzer.analyze_pair(pair, notify=False)
            kw = _mock_data_and_strategy["last"]
            seen[pair] = (kw["sl_frac"], kw["tp_frac"])

        assert seen["BTC/USDT:USDT"] == (0.004, 0.020)
        assert seen["ETH/USDT:USDT"] == (0.006, 0.030)
        assert seen["SOL/USDT:USDT"] == (0.012, 0.040)
        assert seen["XRP/USDT:USDT"] == (0.010, 0.050)


class TestAnalyzerPerPairPoiTolerance:
    """Fix 12.A: per-pair POI tolerance flows through analyze_pair.

    The WFO scoreboard showed winning POI tolerance varies 0.0015 →
    0.01 across pairs — analyze_pair must read settings.get_poi_tap_tolerance(pair)
    so each pair gets its own value live."""

    def test_btc_falls_back_to_global_when_no_override(self, _mock_data_and_strategy, monkeypatch):
        monkeypatch.setattr(settings, "poi_tap_tolerance", 0.005)
        monkeypatch.setattr(settings, "poi_tap_tolerance_btc", None)
        analyzer.analyze_pair("BTC/USDT:USDT", notify=False)
        kw = _mock_data_and_strategy["last"]
        assert kw["poi_tolerance"] == 0.005

    def test_sol_uses_per_pair_override(self, _mock_data_and_strategy, monkeypatch):
        # SOL's WFO winner is poi=0.01 — looser tolerance than BTC's 0.0015.
        monkeypatch.setattr(settings, "poi_tap_tolerance", 0.005)
        monkeypatch.setattr(settings, "poi_tap_tolerance_sol", 0.01)
        analyzer.analyze_pair("SOL/USDT:USDT", notify=False)
        kw = _mock_data_and_strategy["last"]
        assert kw["poi_tolerance"] == 0.01

    def test_each_pair_reads_its_own_poi(self, _mock_data_and_strategy, monkeypatch):
        """A single scan over the 4 configured pairs must produce 4
        distinct POI tolerances matching the WFO scoreboard."""
        monkeypatch.setattr(settings, "poi_tap_tolerance", 0.005)
        monkeypatch.setattr(settings, "poi_tap_tolerance_btc", 0.0015)
        monkeypatch.setattr(settings, "poi_tap_tolerance_eth", 0.005)
        monkeypatch.setattr(settings, "poi_tap_tolerance_sol", 0.01)
        monkeypatch.setattr(settings, "poi_tap_tolerance_xrp", 0.003)

        seen = {}
        for pair in [
            "BTC/USDT:USDT",
            "ETH/USDT:USDT",
            "SOL/USDT:USDT",
            "XRP/USDT:USDT",
        ]:
            analyzer.analyze_pair(pair, notify=False)
            kw = _mock_data_and_strategy["last"]
            seen[pair] = kw["poi_tolerance"]

        assert seen["BTC/USDT:USDT"] == 0.0015
        assert seen["ETH/USDT:USDT"] == 0.005
        assert seen["SOL/USDT:USDT"] == 0.01
        assert seen["XRP/USDT:USDT"] == 0.003
