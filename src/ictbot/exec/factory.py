"""
Live-broker factory.

Reads `settings.exchange` and returns the matching live broker class
(DeltaLiveBroker or BinanceLiveBroker). Mirrors `ictbot.data.factory` so
the scanner can stay venue-agnostic:

    broker = build_live_broker(allowed_pairs=set(PAIRS))

Re-exports `LiveTradingDisabled` so the scanner doesn't need to know
which broker raised it — both brokers raise the same exception type by
name (defined per-module).
"""

from __future__ import annotations

# Both broker modules define a `LiveTradingDisabled` class with identical
# semantics (gate refusal). We re-export Delta's by default; Binance's
# is wire-compatible. Tests that need the precise class import from the
# specific broker module.
from ictbot.exec.delta_live import LiveTradingDisabled  # noqa: F401  (re-export)
from ictbot.settings import settings


def build_live_broker(allowed_pairs: set[str] | None = None):
    """Construct the live broker for the configured venue.

    Imports are deferred so a one-venue test environment doesn't load
    the other broker's module path."""
    name = settings.exchange.lower()
    if name == "delta":
        from ictbot.exec.delta_live import DeltaLiveBroker

        return DeltaLiveBroker(
            allowed_pairs=allowed_pairs,
            api_key=settings.delta_api_key,
            api_secret=settings.delta_api_secret,
        )
    if name == "binance":
        from ictbot.exec.binance_live import BinanceLiveBroker

        # Thread BINANCE_TESTNET through so validation hits
        # testnet.binancefuture.com (no KYC) instead of mainnet.
        return BinanceLiveBroker(
            allowed_pairs=allowed_pairs,
            testnet=settings.binance_testnet,
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
        )
    raise ValueError(f"Unknown EXCHANGE={settings.exchange!r} — expected 'delta' or 'binance'")
