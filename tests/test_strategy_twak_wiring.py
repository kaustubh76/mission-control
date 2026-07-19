"""Per-arm strategy -> TWAK execution-wiring regression tests.

Locks in the audit that found NO arm-specific execution gap: every contest arm (+ mean_reversion)
must (a) be CMC-native (candle_source starts with 'cmc' -> firewall-safe), (b) emit a weight vector
that respects the execution contract (finite, non-negative, sum <= cap) even on degenerate inputs,
and (c) be consumed by the TwakSpotBroker with no failed swap, no overspend, and conserved NAV.

A future arm that returns a NaN/over-cap weight (which silently no-ops in the live broker — `NaN <
-thresh` is False, so it never errors and the gap hides) is caught here at the source instead.
Deterministic + offline: synthetic matrices, SimTwakClient paper broker, no network/keys/spend.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ictbot.exec.bsc_spot_live import TwakSpotBroker
from ictbot.exec.twak_client import make_client
from ictbot.strategy import registry
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS
from ictbot.strategy.registry import StratContext

# Contest set + mean_reversion — the arms in scope for the wiring audit.
ARMS = [
    "momentum_cmc",
    "mean_reversion",
    "dual_momentum",
    "rotation",
    "breakout",
    "momentum_voltarget",
    "momentum_mafilter",
]
K = len(CONTEST_TOKENS)


def _df(arr: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(arr, columns=list(CONTEST_TOKENS))


def _scenarios() -> dict[str, pd.DataFrame]:
    """normal walk + degenerate edges that stress the sizing math."""
    rng = np.random.default_rng(7)
    n = 320
    normal = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, size=(n, K)), axis=0))
    flat = np.full((n, K), 100.0)  # zero-vol cold-start seed -> inverse-vol 1/0 stress
    crash = normal.copy()
    crash[-5:, :] = crash[-6, :] * np.linspace(1.0, 0.65, 5)[:, None]  # all-oversold
    return {"normal": _df(normal), "flat_seed": _df(flat), "crash_oversold": _df(crash)}


SCENARIOS = _scenarios()


@pytest.fixture(autouse=True)
def _builtins():
    registry.register_builtins()


@pytest.mark.parametrize("arm", ARMS)
def test_arm_is_cmc_native(arm):
    # The live entry point sets CMC_ONLY=true, which RAISES on any non-CMC candle path. Every
    # scope arm must source CMC candles (candle_source starts with 'cmc') -> firewall-safe.
    cs = getattr(registry.get(arm), "candle_source", "cmc_4h")
    assert str(cs).startswith("cmc"), f"{arm} candle_source={cs!r} is not CMC -> would RAISE live"


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_arm_weight_contract(arm, scenario):
    strat = registry.get(arm)
    ctx = StratContext(params=strat.default_params(), fear_greed=50)
    w = strat.target_weights_now(SCENARIOS[scenario], ctx=ctx).weights
    assert set(w).issubset(set(CONTEST_TOKENS)), f"{arm}/{scenario}: weight on a non-contest token"
    vals = list(w.values())
    assert all(math.isfinite(v) for v in vals), f"{arm}/{scenario}: non-finite weight (NaN/inf)"
    assert all(v >= -1e-9 for v in vals), f"{arm}/{scenario}: negative weight"
    assert sum(vals) <= 1.0 + 1e-6, f"{arm}/{scenario}: weights over-deploy (sum>{sum(vals):.4f})"


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_arm_broker_consumes_weights(arm, scenario):
    df = SCENARIOS[scenario]
    strat = registry.get(arm)
    ctx = StratContext(params=strat.default_params(), fear_greed=50)
    w = strat.target_weights_now(df, ctx=ctx).weights

    last = {t: float(df[t].iloc[-1]) for t in CONTEST_TOKENS}
    client = make_client("sim", lambda t: 1.0 if t in ("USDT", "USD") else last.get(t, 1.0),
                         start_usdt=1000.0)
    broker = TwakSpotBroker(client, tokens=CONTEST_TOKENS, min_rebal_frac=0.01,
                            min_swap_usd=0.5, live=False)
    rep = broker.rebalance(w)

    assert rep.n_failed == 0, f"{arm}/{scenario}: {rep.n_failed} failed swap(s)"
    assert math.isfinite(rep.nav_after), f"{arm}/{scenario}: NAV went non-finite"
    # paper rebalance must conserve NAV (only fee+slippage bleed, never grow it)
    assert rep.nav_after <= rep.nav_before * 1.0001, f"{arm}/{scenario}: NAV grew"
    assert rep.nav_after >= rep.nav_before * 0.95, f"{arm}/{scenario}: NAV lost >5% to a rebalance"
    for s in rep.swaps:
        assert math.isfinite(s.amount_from) and math.isfinite(s.amount_to), \
            f"{arm}/{scenario}: non-finite swap amount"
