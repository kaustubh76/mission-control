"""
Approximate delta volume: sum of volume on green candles minus sum of
volume on red candles, across the supplied DataFrame.

Positive = net buying pressure, negative = net selling pressure.
"""

import pandas as pd


def get_delta(df: pd.DataFrame) -> float:
    buy_volume = df.loc[df["close"] > df["open"], "volume"].sum()
    sell_volume = df.loc[df["close"] < df["open"], "volume"].sum()
    return round(float(buy_volume - sell_volume), 2)


def get_cvd(symbol: str, bar_time: pd.Timestamp, exchange) -> float | None:
    """E4 (ROADMAP §E4): real CVD over the last bar's window.

    Requires an exchange with `fetch_cvd(symbol, since_ms, until_ms)`.
    `bar_time` is the timestamp of the bar we want CVD for; the window
    is [bar_time - 1min, bar_time] (the 1m bar that just closed).

    Returns None if the exchange doesn't expose `fetch_cvd` (signals
    fallback to the candle-color proxy in get_delta).
    """
    if not hasattr(exchange, "fetch_cvd"):
        return None
    try:
        end_ms = int(pd.Timestamp(bar_time).value // 1_000_000)
        start_ms = end_ms - 60_000  # 1m bar
        return float(exchange.fetch_cvd(symbol, start_ms, end_ms))
    except Exception:
        return None


def get_relative_delta(df: pd.DataFrame, window: int = 20) -> float:
    """B3 (ROADMAP §B3): delta normalised to recent volume regime.

    Absolute-sign delta has two problems:
      1. A tiny +ε on a low-volume bar reads as bullish — but it's noise.
      2. A massive -1000 on a high-volume bar reads as bearish — but it
         may be in line with the prior 20 bars' average, not a step change.

    Relative delta = delta / median(|signed_volume per bar|, last `window` bars).
    Returns 0.0 if there's no history yet (so the strategy treats it as
    neither side significant, not as a fake bias).

    Caller is expected to threshold the result, e.g. > +0.5 = strong buy.
    """
    if len(df) < window + 1:
        return 0.0
    signed_per_bar = (df["close"] > df["open"]).astype(int) * df["volume"] - (
        df["close"] < df["open"]
    ).astype(int) * df["volume"]
    recent = signed_per_bar.tail(window)
    scale = recent.abs().median()
    if scale <= 0:
        return 0.0
    current = float(signed_per_bar.iloc[-1])
    return round(current / float(scale), 3)
