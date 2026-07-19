"""
G1 (ROADMAP §G1) — typed Signal dataclass.

The strategy contract has long been "evaluate() returns a dict of keys"
which forces 25+ callers to unpack by string. This module gives them a
typed view of the same data.

Initial shipping plan (this commit): introduce the dataclass with
from_dict/to_dict round-trip + acceptance tests. ICTProMaxStrategy still
returns the legacy dict so we don't churn every caller in one PR; new
code can opt in by wrapping the dict in `Signal.from_dict(result)`. The
full migration is tracked in ROADMAP §G1 follow-ups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SignalSide = Literal["BUY", "SELL", "NO ENTRY"]


@dataclass(frozen=True)
class Signal:
    """A single evaluation of the strategy. All numeric fields are floats;
    string fields use the same labels emitted by the legacy dict so we
    can round-trip without information loss."""

    # Identity
    pair: str
    error: str | None = None

    # Prices
    price: float = 0.0
    last_close: float = 0.0

    # ICT stack
    htf_bias: str = "N/A"
    ltf_bias: str = "N/A"
    ltf_poi: float = 0.0
    poi_tap: str = "N/A"
    ltf_mss: str = "N/A"
    fvg: str = "N/A"
    micro_fvg: str = "N/A"
    delta: float = 0.0
    relative_delta: float = 0.0
    delta_mode: str = "sign"
    atr_1m: float = 0.0

    # Signal
    entry: SignalSide = "NO ENTRY"
    sl: float = 0.0
    tp: float = 0.0
    rr: float = 0.0
    confidence: int = 0

    # Gates / regime
    gate_blocked: str | None = None
    regime: str | None = None

    # Diagnostics
    diagnostics: dict = field(default_factory=dict)

    # ---- round-trip with the legacy dict ---------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> Signal:
        """Build a Signal from the dict shape that ICTProMaxStrategy.evaluate
        emits. Unknown keys are dropped; missing keys default."""
        valid = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in d.items() if k in valid}
        return cls(**kwargs)

    def to_dict(self) -> dict:
        """Mirror the legacy dict keys exactly."""
        return {
            "pair": self.pair,
            "error": self.error,
            "price": self.price,
            "last_close": self.last_close,
            "htf_bias": self.htf_bias,
            "ltf_bias": self.ltf_bias,
            "ltf_poi": self.ltf_poi,
            "poi_tap": self.poi_tap,
            "ltf_mss": self.ltf_mss,
            "fvg": self.fvg,
            "micro_fvg": self.micro_fvg,
            "delta": self.delta,
            "relative_delta": self.relative_delta,
            "delta_mode": self.delta_mode,
            "atr_1m": self.atr_1m,
            "entry": self.entry,
            "sl": self.sl,
            "tp": self.tp,
            "rr": self.rr,
            "confidence": self.confidence,
            "gate_blocked": self.gate_blocked,
            "regime": self.regime,
            "diagnostics": self.diagnostics,
        }

    # ---- convenience -----------------------------------------------------

    def is_actionable(self) -> bool:
        """True iff the signal carries a tradeable direction with no error."""
        return self.entry in ("BUY", "SELL") and self.error is None

    def risk_distance(self) -> float:
        """Absolute price distance from entry to SL. Used for sizing."""
        if not self.is_actionable():
            return 0.0
        return abs(self.price - self.sl)
