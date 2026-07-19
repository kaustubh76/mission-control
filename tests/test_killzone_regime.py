"""Tests for Phase 7 — killzone gate + regime filter."""

import pandas as pd

from ictbot.indicators.regime import atr_percentile_regime
from ictbot.runtime.sessions import is_killzone_active
from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _flat_session(killzone: bool) -> dict:
    return {
        "india_time": "00:00:00",
        "tokyo_time": "00:00:00",
        "tokyo_status": "CLOSED",
        "london_time": "00:00:00",
        "london_status": "OPEN" if killzone else "CLOSED",
        "newyork_time": "00:00:00",
        "newyork_status": "CLOSED",
        "active_session": "OFF HOURS",
        "allow_trade": True,
        "killzone_active": killzone,
    }


def test_is_killzone_active_reads_dict():
    assert is_killzone_active(_flat_session(True)) is True
    assert is_killzone_active(_flat_session(False)) is False


def _df(n, vol_factor=1.0):
    """Synthetic OHLCV — wide range when vol_factor>1, tight when <1."""
    return pd.DataFrame(
        {
            "time": pd.to_datetime([i * 60_000 for i in range(n)], unit="ms"),
            "open": [100.0] * n,
            "high": [100.0 + 1.0 * vol_factor] * n,
            "low": [100.0 - 1.0 * vol_factor] * n,
            "close": [100.0] * n,
            "volume": [10] * n,
        }
    )


def test_regime_high_vol_when_recent_atr_above_window():
    # 200 quiet bars then 50 wild bars — current ATR ranks at the top.
    quiet = _df(200, vol_factor=0.1)
    wild = _df(50, vol_factor=5.0)
    df = pd.concat([quiet, wild], ignore_index=True)
    df["time"] = pd.to_datetime(range(len(df)), unit="m")
    assert atr_percentile_regime(df) == "HIGH_VOL"


def test_regime_normal_when_history_too_short():
    df = _df(20)
    assert atr_percentile_regime(df) == "NORMAL"


def test_killzone_gate_blocks_entries_when_required_and_closed():
    # Build frames that would otherwise produce a setup. We don't need
    # an actual signal — we just need to verify gate_blocked is set.
    strat = ICTProMaxStrategy(killzone_required=True, strategy_mode="follow")
    htf = _df(60)
    bias = _df(30)
    poi = _df(30)
    entry = _df(20)

    out = strat.evaluate(htf, bias, poi, entry, _flat_session(False))
    assert out["entry"] == "NO ENTRY"
    assert out["gate_blocked"] == "outside killzone (London/NY closed)"


def test_killzone_gate_inactive_when_not_required():
    strat = ICTProMaxStrategy(strategy_mode="follow")  # killzone_required=False
    out = strat.evaluate(_df(60), _df(30), _df(30), _df(20), _flat_session(False))
    assert out["gate_blocked"] is None
