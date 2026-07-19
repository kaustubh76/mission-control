"""
Account — minimal equity tracker driven by closed orders.

Holds two numbers (starting balance + current equity) and a list of
closed-trade R-multiples. Phase 9 will plumb this into the journal so
equity is durable across restarts; for now it's in-memory only and the
backtest engine constructs one per run.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Account:
    starting_balance: float
    equity: float = 0.0
    closed_R: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.equity == 0.0:
            self.equity = self.starting_balance

    def book_close(self, r_multiple: float, risk_pct: float = 0.01) -> None:
        """Apply an R-multiple PnL to the running equity.

        risk_pct is the fraction of equity that was at risk on the trade
        (defaults to 1 %). Equity grows by `equity * risk_pct * R`.
        """
        delta = self.equity * risk_pct * r_multiple
        self.equity += delta
        self.closed_R.append(r_multiple)

    @property
    def total_R(self) -> float:
        return sum(self.closed_R)

    @property
    def drawdown(self) -> float:
        """Largest peak-to-trough fraction. 0.0 = no drawdown."""
        peak = self.starting_balance
        max_dd = 0.0
        eq = self.starting_balance
        for r in self.closed_R:
            eq += self.starting_balance * 0.01 * r
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak else 0.0
            max_dd = max(max_dd, dd)
        return max_dd
