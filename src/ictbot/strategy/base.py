"""
Strategy abstract base class.

A Strategy turns four DataFrames (HTF/15m/3m/1m) plus a session dict
into a "result dict" that has a stable shape consumed by the dashboard,
the scanner, and the backtest. Phase 11 will add a typed Signal
dataclass and migrate callers to it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """Strategy contract: pure, no I/O.

    Subclasses configure themselves at construction time (which bias
    engine, which POI engine, sl/tp distances, etc.) and then take
    `(htf_df, bias_df, poi_df, entry_df, session)` and return a dict.
    """

    @abstractmethod
    def evaluate(
        self,
        htf_df: pd.DataFrame,
        bias_df: pd.DataFrame,
        poi_df: pd.DataFrame,
        entry_df: pd.DataFrame,
        session: dict,
        pair: str = "TEST",
    ) -> dict: ...
