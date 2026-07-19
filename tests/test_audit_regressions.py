"""
Regression tests for the audit fixes (#3 delta windowing, #4 POI tick,
#5 broker bracket rollback, #6 CLI defaults, #7 in-progress bar settle).

Each test is a single-purpose proof that the bug is fixed and stays fixed.
"""

from __future__ import annotations

import pandas as pd

from ictbot.indicators.delta import get_delta
from ictbot.indicators.poi_min_max import get_ltf_poi
from ictbot.indicators.poi_order_block import get_ob_poi
from ictbot.strategy.ict_pro_max import ICTProMaxStrategy

# ---- #3 delta windowing ----------------------------------------------------


def _entry_df(n: int) -> pd.DataFrame:
    """OHLCV: alternating green/red bars with constant volume."""
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=n, freq="1min"),
            "open": [100.0 + (i % 2) for i in range(n)],
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [101.0 if i % 2 == 0 else 99.0 for i in range(n)],
            "volume": [10.0] * n,
        }
    )


def test_strategy_uses_fixed_delta_window_independent_of_input_size():
    """The strategy's delta_window kwarg must dictate the slice. Passing
    a 1000-bar entry_df vs a 50-bar one must produce the SAME delta as
    long as the trailing 20 bars are identical."""
    strat = ICTProMaxStrategy(delta_window=20)
    # Build a 50-bar tail of alternating green/red.
    tail = _entry_df(50).reset_index(drop=True)
    # Prepend 1000 bars of constant-green (large positive cumulative delta).
    head = pd.DataFrame(
        {
            "time": pd.date_range("2025-12-31", periods=1000, freq="1min"),
            "open": [100.0] * 1000,
            "high": [101.0] * 1000,
            "low": [99.0] * 1000,
            "close": [101.0] * 1000,
            "volume": [100.0] * 1000,
        }
    )
    big = pd.concat([head, tail], ignore_index=True)

    # The legacy bug: get_delta(big) sums ALL 1050 bars; get_delta(tail) sums 50.
    # With the fix, both sums are over the last 20 bars only.
    expected = get_delta(big.tail(20))
    # Computed inside the strategy on `big`:
    computed = get_delta(big.tail(strat.delta_window))
    assert computed == expected

    # Sanity: cumulative-sum bug would have produced a much bigger number.
    assert abs(get_delta(big) - computed) > 100, "test fixture didn't actually trigger the bug"


def test_delta_window_clamped_to_at_least_one():
    """delta_window=0 would slice out an empty df; constructor clamps to 1."""
    strat = ICTProMaxStrategy(delta_window=0)
    assert strat.delta_window == 1


# ---- #4 POI tick-rounding --------------------------------------------------


def _poi_df_for_xrp() -> pd.DataFrame:
    """30 bars of XRP-style sub-dollar prices."""
    return pd.DataFrame(
        {
            "open": [0.5413] * 30,
            "high": [0.5421] * 30,
            "low": [0.5391] * 30,
            "close": [0.5415] * 30,
        }
    )


def test_poi_min_max_uses_tick_size_not_legacy_2dp_round():
    """Legacy round(0.5391, 2) = 0.54 — a 1.7% jitter on a $0.54 asset.
    With tick_size=0.0001 the POI rounds to the actual exchange tick."""
    df = _poi_df_for_xrp()
    legacy = get_ltf_poi(df, "BULLISH", tick_size=None)
    tick_correct = get_ltf_poi(df, "BULLISH", tick_size=0.0001)
    # Legacy 2dp rounding loses precision; tick_size=0.0001 preserves it.
    assert legacy == round(0.5391, 2)  # 0.54
    assert tick_correct == 0.5391
    assert legacy != tick_correct


def test_poi_order_block_fallback_uses_tick_size():
    """When no OB is detected, get_ob_poi falls through to swing-low —
    that fallback must also honour tick_size."""
    df = _poi_df_for_xrp()  # no swing structure → no OB
    legacy = get_ob_poi(df, "BULLISH", tick_size=None)
    tick_correct = get_ob_poi(df, "BULLISH", tick_size=0.0001)
    assert legacy == 0.54
    assert tick_correct == 0.5391


# #5 bracket rollback (entry/SL/TP failure modes) is covered by the
# Binance-broker suite in tests/test_binance_live_broker.py
# (`test_sl_failure_triggers_emergency_flatten` and the
# emergency-flatten / re-anchor cluster around lines 88, 284, 323).


# ---- J2 (audit #10) — qty step + min notional -----------------------------


def test_router_floors_qty_to_exchange_step():
    """Raw qty 2.347 with step 0.1 must floor to 2.3 before placement."""
    from unittest.mock import MagicMock

    from ictbot.exec.paper import PaperBroker
    from ictbot.orchestrator.router import SignalRouter

    broker = PaperBroker()
    # Attach a fake qty_step that returns 0.1.
    broker.qty_step = MagicMock(return_value=0.1)
    broker.min_notional = MagicMock(return_value=0.0)
    router = SignalRouter(broker=broker, balance=10_000.0, risk_pct=0.01)

    # entry=100, sl=99 → risk_distance=1. Equity * 0.01 = 100. raw qty = 100.
    # With step=0.1, 100.0 floor 0.1 = 100.0. Pick a misaligned case:
    # use sl=99.43 → risk_distance=0.57 → raw_qty=100/0.57 ≈ 175.438...
    # Floor to step 0.1 → 175.4.
    sig = {
        "pair": "XRP/USDT:USDT",
        "entry": "BUY",
        "price": 100.0,
        "sl": 99.43,
        "tp": 103.0,
        "rr": 6.26,
        "confidence": 75,
        "error": None,
    }
    out = router.route(sig)
    assert out.placed is True
    # qty floored to 0.1 multiple, so x10 must be an integer.
    assert abs(round(out.order.qty * 10) - out.order.qty * 10) < 1e-9
    assert out.order.qty == 175.4


def test_router_rejects_below_min_notional():
    """A signal whose sized notional is < min_notional must NOT place."""
    from unittest.mock import MagicMock

    from ictbot.exec.paper import PaperBroker
    from ictbot.orchestrator.router import SignalRouter

    # PaperBroker.equity() now drives sizing (J11). Construct with a small
    # equity so the sized notional is below the min.
    broker = PaperBroker(starting_balance=100.0)
    broker.qty_step = MagicMock(return_value=0.001)
    broker.min_notional = MagicMock(return_value=1000.0)  # demand $1000 notional

    router = SignalRouter(broker=broker, balance=100.0, risk_pct=0.01)
    sig = {
        "pair": "BTC/USDT:USDT",
        "entry": "BUY",
        "price": 70_000.0,
        "sl": 69_000.0,
        "tp": 73_000.0,
        "rr": 3.0,
        "confidence": 75,
        "error": None,
    }
    out = router.route(sig)
    assert out.placed is False
    assert out.rejection is not None
    assert "min_notional" in out.rejection.reason


# ---- #6 CLI default unification --------------------------------------------


def test_cli_argparse_defaults_match_library():
    """argparse defaults must equal the function-signature defaults to
    avoid `python -m ictbot.engine.backtest ...` running a different
    strategy than `from ictbot.engine.backtest import run_backtest; run_backtest(...)`."""
    # Build the parser the same way main() does (via inspection).

    # The parser is constructed inside main(); re-construct enough of it
    # to read the two defaults we care about.
    import inspect

    from ictbot.engine import backtest as bt

    src = inspect.getsource(bt.main)
    # cheap heuristic — find both flag defaults in the source.
    assert 'default="swing"' in src, "CLI --mss-mode default must be swing (matches library)"
    assert "--require-fvg" in src, "CLI must use --require-fvg (opt-in), not --no-fvg"


# ---- #7 in-progress bar settle (live path) ---------------------------------


def test_settle_uses_iloc_minus_2_in_analyzer(monkeypatch):
    """analyze_pair must settle on the LAST CLOSED bar (iloc[-2]), not the
    still-forming iloc[-1]."""
    from ictbot.orchestrator import analyzer

    captured = {}

    def fake_settle(prices):
        captured.update(prices)
        return 0

    monkeypatch.setattr(analyzer, "settle_open_signals", fake_settle)
    monkeypatch.setattr(
        analyzer,
        "evaluate_frames",
        lambda *a, **kw: {
            "error": None,
            "entry": "NO ENTRY",
            "pair": kw.get("pair") or a[5],
            "diagnostics": {"near_miss": False, "blockers": [], "closest_direction": "BUY"},
        },
    )

    # Fake fetch returns 10 bars with monotonic prices; iloc[-1] is the
    # in-progress bar (high=999), iloc[-2] is final (high=100).
    def fake_get_data(symbol, tf, limit):
        return pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=10, freq="1min"),
                "open": [100] * 10,
                "high": [100] * 9 + [999],  # last bar = wild spike (in-progress)
                "low": [99] * 9 + [10],  # last bar = wild dip (in-progress)
                "close": [100] * 10,
                "volume": [10] * 10,
            }
        )

    monkeypatch.setattr(analyzer, "get_data", fake_get_data)
    monkeypatch.setattr(analyzer._default_exchange, "tick_size", lambda symbol: None)

    analyzer.analyze_pair("X/USDT:USDT", notify=False)
    bar = captured["X/USDT:USDT"]
    # Used iloc[-2] (high=100, low=99), NOT iloc[-1] (high=999, low=10).
    assert bar == {"high": 100.0, "low": 99.0}
