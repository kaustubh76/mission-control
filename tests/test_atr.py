import pandas as pd

from ictbot.indicators.atr import get_atr


def test_returns_zero_when_too_few_bars():
    df = pd.DataFrame(
        {
            "high": [1, 2, 3],
            "low": [0, 1, 2],
            "close": [1, 2, 3],
            "open": [1, 2, 3],
            "volume": [1, 1, 1],
        }
    )
    assert get_atr(df, period=14) == 0.0


def test_constant_range_yields_that_range():
    # Every bar has range 2 (high - low). True range will be 2 for all bars
    # (since |high - prev_close| = 1, |low - prev_close| = 1, max(2,1,1) = 2).
    n = 30
    df = pd.DataFrame(
        {
            "open": [10] * n,
            "high": [11] * n,
            "low": [9] * n,
            "close": [10] * n,
            "volume": [1] * n,
        }
    )
    assert get_atr(df, period=14) == 2.0


def test_atr_responds_to_recent_volatility():
    # 20 calm bars then 10 volatile bars; ATR(14) on the latest should reflect the spike.
    calm = [{"open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": 1} for _ in range(20)]
    spike = [{"open": 10, "high": 15, "low": 5, "close": 10, "volume": 1} for _ in range(10)]
    df = pd.DataFrame(calm + spike)
    # ATR is over last 14 bars; 10 of those are spike (TR ~10), 4 are calm (TR ~0.2)
    atr = get_atr(df, period=14)
    assert atr > 5.0  # well above calm range
    assert atr < 11.0  # but not pure spike value


def _slow_atr(df, period: int = 14) -> float:
    """The old, O(n)-per-call ATR. Kept as a reference oracle for regression."""
    if len(df) < period + 1:
        return 0.0
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(round(tr.tail(period).mean(), 6))


def test_optimised_atr_equals_slow_atr_on_long_series():
    """Regression: the tail-slice optimisation in get_atr must yield
    the SAME number as the legacy whole-series approach.

    Replays a synthetic 500-bar series and compares both implementations
    bit-for-bit (after the 6-decimal round). If this test fails,
    something in the slice-and-shift logic shifted by a row.
    """
    n = 500
    df = pd.DataFrame(
        {
            "open": [100 + 0.1 * i for i in range(n)],
            "high": [101 + 0.1 * i for i in range(n)],
            "low": [99 + 0.1 * i for i in range(n)],
            "close": [100.5 + 0.1 * i for i in range(n)],
            "volume": [10] * n,
        }
    )

    # Sweep a few common periods to exercise multiple tail-window sizes.
    for period in (7, 14, 21, 50):
        fast = get_atr(df, period=period)
        slow = _slow_atr(df, period=period)
        assert fast == slow, f"period={period}: fast={fast} slow={slow}"


def test_optimised_atr_equals_slow_atr_with_volatility_spikes():
    """As above but with realistic volatility — spikes, gaps, doji bars."""
    import random

    rng = random.Random(7)
    n = 300
    rows = []
    price = 100.0
    for i in range(n):
        price += rng.uniform(-1.0, 1.0)
        spike = 5.0 if i % 17 == 0 else 0.5
        rows.append(
            {
                "open": price,
                "high": price + spike,
                "low": price - spike,
                "close": price + rng.uniform(-0.3, 0.3),
                "volume": rng.uniform(1, 100),
            }
        )
    df = pd.DataFrame(rows)
    assert get_atr(df, period=14) == _slow_atr(df, period=14)
    assert get_atr(df, period=21) == _slow_atr(df, period=21)
