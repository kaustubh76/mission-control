"""
Slope-based bias: less lag than SMA crossover.

Computes an EMA of `close` over `period` bars, then measures the slope of
that EMA across the last `window` bars (linear regression slope). Positive
slope = BULLISH, negative = BEARISH.

Compared to SMA(20) vs SMA(50) crossover, this responds ~3x faster to a
trend turn — useful because the lagging crossover was empirically firing
right at trend exhaustion (see docs/findings.md).
"""

import pandas as pd


def get_slope_bias(df: pd.DataFrame, period: int = 20, window: int = 5) -> str:
    """Return 'BULLISH' or 'BEARISH' based on EMA slope.

    Fallback to last-vs-first close comparison if there's not enough data.
    """
    if len(df) < period + window:
        if len(df) < 2:
            return "BULLISH"  # arbitrary
        return "BULLISH" if df["close"].iloc[-1] > df["close"].iloc[0] else "BEARISH"

    ema = df["close"].ewm(span=period, adjust=False).mean()
    recent = ema.tail(window).reset_index(drop=True)
    # Linear regression slope on [0, 1, .., window-1] vs recent EMA values.
    n = len(recent)
    x_mean = (n - 1) / 2.0
    y_mean = recent.mean()
    num = sum((i - x_mean) * (recent.iloc[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    return "BULLISH" if slope > 0 else "BEARISH"
