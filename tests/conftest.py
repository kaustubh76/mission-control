"""
Pytest config — provides reusable synthetic OHLCV fixtures so unit
tests never hit the network. The package is installed editable via
`pip install -e .`, so no sys.path manipulation is needed.
"""

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _no_real_x402(monkeypatch):
    """Safety net: the test suite must NEVER spend real USDC. The sim-tick hardening
    tests run a real allocator tick that calls the live x402 dex_search when
    X402_ENABLED is on in the dev .env AND the payment wallet is funded — which would
    settle on-chain. Force x402 off for every test; the one test that exercises the
    enabled branch re-enables it AND mocks dex_search, so it still never pays."""
    try:
        from ictbot.settings import settings

        monkeypatch.setattr(settings, "x402_enabled", False, raising=False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_network_cmc_levers(monkeypatch):
    """Same safety net for the CMC network levers. The campaign .env (2026-06-13)
    enables intel/TA/MCP/skill for the forward cron — but a tick run inside a test
    would then hit the live CMC Agent Hub / Startup endpoints (slow, flaky, burns
    credits). Force them off for every test; tests that exercise a lever re-enable
    it explicitly AND mock the transport."""
    try:
        from ictbot.settings import settings

        for flag in (
            "cmc_intel_enabled",
            "cmc_regime_enhanced",
            "alloc_ta_enabled",
            "cmc_mcp_enabled",
            "cmc_skill_regime",
        ):
            monkeypatch.setattr(settings, flag, False, raising=False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_pushed_snapshot(tmp_path, monkeypatch):
    """Safety net: a stray local `data/_pushed_snapshot.json` (the live-push dashboard override) must
    NOT bleed into `/api/snapshot` tests. Point `reads.PUSHED` at an absent per-test tmp so the route
    serves the journal-computed snapshot by default. (test_ingest's own `pushed_tmp` overrides this to
    the same per-test tmp when it exercises the push path.)"""
    try:
        import ictbot.api.reads as reads

        monkeypatch.setattr(reads, "PUSHED", tmp_path / "_pushed_snapshot.json", raising=False)
    except Exception:
        pass


def _ohlcv(rows):
    """rows = list of (open, high, low, close, volume)."""
    return pd.DataFrame(
        [
            {
                "time": pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=i),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
            for i, (o, h, l, c, v) in enumerate(rows)
        ]
    )


@pytest.fixture
def bullish_df():
    """A clearly up-trending series."""
    rows = [(100 + i, 101 + i, 99 + i, 100.5 + i, 10) for i in range(100)]
    return _ohlcv(rows)


@pytest.fixture
def bearish_df():
    """A clearly down-trending series."""
    rows = [(100 - i, 101 - i, 99 - i, 99.5 - i, 10) for i in range(100)]
    return _ohlcv(rows)


@pytest.fixture
def flat_df():
    """A flat series — neither bias."""
    rows = [(100, 101, 99, 100, 10) for _ in range(100)]
    return _ohlcv(rows)


@pytest.fixture
def bullish_mss_df():
    """Last bar makes a higher high vs prev bar."""
    rows = [(100, 101, 99, 100, 10) for _ in range(10)]
    rows[-2] = (100, 102, 99, 100, 10)  # prev high = 102
    rows[-1] = (100, 105, 99, 100, 10)  # last high = 105 (BULLISH MSS)
    return _ohlcv(rows)


@pytest.fixture
def bearish_mss_df():
    """Last bar makes a lower low vs prev bar."""
    rows = [(100, 101, 99, 100, 10) for _ in range(10)]
    rows[-2] = (100, 101, 98, 100, 10)  # prev low = 98
    rows[-1] = (100, 101, 95, 100, 10)  # last low = 95 (BEARISH MSS)
    return _ohlcv(rows)


@pytest.fixture
def bullish_fvg_df():
    """3-candle imbalance to the upside: low[-1] > high[-3]."""
    rows = [
        (100, 101, 99, 100, 10),
        (100, 101, 99, 100, 10),
        (100, 102, 99, 101, 10),  # candle[-3]: high = 102
        (101, 104, 100, 103, 10),  # gap-up candle
        (105, 110, 103, 108, 10),  # candle[-1]: low = 103 > 102 ✓
    ]
    return _ohlcv(rows)


@pytest.fixture
def bearish_fvg_df():
    """3-candle imbalance to the downside: high[-1] < low[-3]."""
    rows = [
        (100, 101, 99, 100, 10),
        (100, 101, 99, 100, 10),
        (100, 101, 98, 99, 10),  # candle[-3]: low = 98
        (99, 100, 95, 96, 10),  # gap-down candle
        (95, 97, 92, 94, 10),  # candle[-1]: high = 97 < 98 ✓
    ]
    return _ohlcv(rows)


@pytest.fixture
def buy_pressure_df():
    """More green volume than red — positive delta."""
    rows = []
    for i in range(20):
        if i % 5 == 0:
            rows.append((100, 101, 99, 99, 5))  # red, small
        else:
            rows.append((100, 101, 99, 101, 10))  # green, big
    return _ohlcv(rows)


@pytest.fixture
def sell_pressure_df():
    """More red volume than green — negative delta."""
    rows = []
    for i in range(20):
        if i % 5 == 0:
            rows.append((100, 101, 99, 101, 5))  # green, small
        else:
            rows.append((100, 101, 99, 99, 10))  # red, big
    return _ohlcv(rows)
