"""
Average True Range — volatility measure for sizing stops.

True Range per bar = max of:
  - high - low
  - |high - prev_close|
  - |low  - prev_close|

ATR(n) = simple moving average of the last n true ranges.
"""

import pandas as pd


def get_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Return the latest ATR value, or 0.0 if not enough data.

    Only the last `period + 1` rows are needed (`period` for the mean,
    one extra so the first TR can use its previous close). Previously
    we computed the TR series over the entire df and then `.tail(period)`-ed,
    which made get_atr O(n) per call and ate 67 % of run_backtest's
    runtime in profiling on 50 000-bar sweeps.
    """
    if len(df) < period + 1:
        return 0.0
    tail = df.tail(period + 1)
    high = tail["high"]
    low = tail["low"]
    close_prev = tail["close"].shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # The first row's close_prev is NaN (no bar before the tail-window
    # head); drop it before averaging.
    atr = tr.iloc[1:].mean()
    return float(round(atr, 6))
