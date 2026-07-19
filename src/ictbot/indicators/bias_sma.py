"""
Higher- and lower-timeframe bias using simple moving averages as a
stand-in for trend. Returns 'BULLISH' or 'BEARISH'.
"""

import pandas as pd


def get_htf_bias(df: pd.DataFrame) -> str:
    """4h bias: SMA20 vs SMA50."""
    sma20 = df["close"].rolling(20).mean().iloc[-1]
    sma50 = df["close"].rolling(50).mean().iloc[-1]
    return "BULLISH" if sma20 > sma50 else "BEARISH"


def get_ltf_bias(df: pd.DataFrame) -> str:
    """15m bias: SMA10 vs SMA20."""
    sma10 = df["close"].rolling(10).mean().iloc[-1]
    sma20 = df["close"].rolling(20).mean().iloc[-1]
    return "BULLISH" if sma10 > sma20 else "BEARISH"
