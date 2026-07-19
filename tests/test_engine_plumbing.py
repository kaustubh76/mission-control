"""
Confirm that new strategy knobs (mss_mode, mitigation_bars, tick_size)
are forwarded from run_backtest → evaluate_frames → ICTProMaxStrategy.

Doesn't check correctness of the knobs themselves (other tests do that)
— only that they reach the strategy intact. This is the lock against
the kind of plumbing bug that would silently leave WFO measuring
default behaviour even when --mss-mode swing is on the command line.
"""

import pandas as pd

from ictbot.engine import backtest


def _stub_history(n_entry: int = 30) -> dict:
    """Just enough rows for run_backtest to enter the replay loop."""
    return {
        "htf": pd.DataFrame(
            {
                "time": pd.to_datetime([i * 60_000 for i in range(60)], unit="ms"),
                "open": [100.0] * 60,
                "high": [101.0] * 60,
                "low": [99.0] * 60,
                "close": [100.0] * 60,
                "volume": [10] * 60,
            }
        ),
        "bias": pd.DataFrame(
            {
                "time": pd.to_datetime([i * 60_000 for i in range(30)], unit="ms"),
                "open": [100.0] * 30,
                "high": [101.0] * 30,
                "low": [99.0] * 30,
                "close": [100.0] * 30,
                "volume": [10] * 30,
            }
        ),
        "poi": pd.DataFrame(
            {
                "time": pd.to_datetime([i * 60_000 for i in range(30)], unit="ms"),
                "open": [100.0] * 30,
                "high": [101.0] * 30,
                "low": [99.0] * 30,
                "close": [100.0] * 30,
                "volume": [10] * 30,
            }
        ),
        "entry": pd.DataFrame(
            {
                "time": pd.to_datetime([i * 60_000 for i in range(n_entry)], unit="ms"),
                "open": [100.0] * n_entry,
                "high": [101.0] * n_entry,
                "low": [99.0] * n_entry,
                "close": [100.0] * n_entry,
                "volume": [10] * n_entry,
            }
        ),
    }


def test_run_backtest_forwards_new_knobs_to_strategy(monkeypatch):
    captured: list[dict] = []

    real_evaluate = backtest.evaluate_frames

    def spy(*a, **kw):
        captured.append(
            {
                "mss_mode": kw.get("mss_mode"),
                "mitigation_bars": kw.get("mitigation_bars"),
                "tick_size": kw.get("tick_size"),
            }
        )
        return real_evaluate(*a, **kw)

    monkeypatch.setattr(backtest, "evaluate_frames", spy)

    backtest.run_backtest(
        "BTC/USDT:USDT",
        bars=20,
        quiet=True,
        history=_stub_history(30),
        mss_mode="swing",
        mitigation_bars=10,
        tick_size=0.5,
    )

    assert captured, "evaluate_frames was never called"
    # Every call must carry the explicit knob values.
    assert all(c["mss_mode"] == "swing" for c in captured)
    assert all(c["mitigation_bars"] == 10 for c in captured)
    assert all(c["tick_size"] == 0.5 for c in captured)


def test_run_backtest_defaults_preserve_legacy_behaviour(monkeypatch):
    captured: list[dict] = []
    real_evaluate = backtest.evaluate_frames

    def spy(*a, **kw):
        captured.append(
            {
                "mss_mode": kw.get("mss_mode"),
                "mitigation_bars": kw.get("mitigation_bars"),
                "tick_size": kw.get("tick_size"),
            }
        )
        return real_evaluate(*a, **kw)

    monkeypatch.setattr(backtest, "evaluate_frames", spy)

    # Disable auto-tick resolution so the test focuses on plumbing rather
    # than on whichever venue happens to be the default. With auto-tick
    # off (returning None), the engine forwards None unchanged.
    monkeypatch.setattr(backtest, "_resolve_tick_size", lambda pair: None)

    backtest.run_backtest("BTC/USDT:USDT", bars=20, quiet=True, history=_stub_history(30))
    assert captured
    # E2 (ROADMAP §E2): default mss_mode flipped "simple" → "swing".
    # Mitigation/tick still default to None (opt-in features).
    assert captured[0]["mss_mode"] == "swing"
    assert captured[0]["mitigation_bars"] is None
    assert captured[0]["tick_size"] is None
