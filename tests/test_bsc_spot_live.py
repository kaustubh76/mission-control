"""Unit tests for the TWAK spot rebalancer broker (sim-client backed)."""

from __future__ import annotations

import pytest

from ictbot.exec.bsc_spot_live import LiveTradingDisabled, TwakSpotBroker
from ictbot.exec.twak_client import SimTwakClient, SwapResult

PRICES = {"BNB": 600.0, "ETH": 3000.0, "CAKE": 2.0}
TOKENS = list(PRICES)


class _FailOnPair:
    """Wrap a SimTwakClient and force ok=False on one (from,to) swap pair —
    simulates a single live swap failing mid-rebalance."""

    def __init__(self, inner, fail_from, fail_to):
        self._inner = inner
        self._ff, self._ft = fail_from, fail_to

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def swap(self, f, t, amt, *, execute=True):
        if f == self._ff and t == self._ft:
            return SwapResult(f, t, amt, 0.0, 0.0, 0.0, tx="", ok=False, error="forced failure")
        return self._inner.swap(f, t, amt, execute=execute)


class _FailFirstN:
    """Fail the (from,to) swap its first N calls, then delegate to inner (which
    succeeds) — simulates a transient leg failure that a retry recovers."""

    def __init__(self, inner, fail_from, fail_to, n):
        self._inner = inner
        self._ff, self._ft, self._n = fail_from, fail_to, n
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def swap(self, f, t, amt, *, execute=True):
        if f == self._ff and t == self._ft and self.calls < self._n:
            self.calls += 1
            return SwapResult(f, t, amt, 0.0, 0.0, 0.0, tx="", ok=False, error="transient")
        return self._inner.swap(f, t, amt, execute=execute)


def make_broker(start_usdt=1000.0, **kw):
    client = SimTwakClient(
        lambda t: PRICES[t], start_usdt=start_usdt, fee_per_side=0.0005, slippage_per_side=0.0010
    )
    return TwakSpotBroker(client, tokens=TOKENS, **kw), client


def test_rebalance_from_cash_deploys_to_targets():
    broker, client = make_broker()
    rep = broker.rebalance({"BNB": 0.36, "ETH": 0.24})  # 60% deployed, rest USDT
    w = rep.weights_after
    assert w["BNB"] == pytest.approx(0.36, abs=0.01)  # within friction
    assert w["ETH"] == pytest.approx(0.24, abs=0.01)
    assert w["CAKE"] == pytest.approx(0.0, abs=1e-6)
    # ~40% stays in USDT (cap respected)
    assert client.balance("USDT") / rep.nav_after == pytest.approx(0.40, abs=0.02)
    assert rep.n_swaps == 2


def test_rebalance_rotates_between_tokens_sell_before_buy():
    broker, client = make_broker()
    broker.rebalance({"BNB": 0.6})  # fully into BNB (of the cap)
    assert client.balance("BNB") > 0
    rep = broker.rebalance({"ETH": 0.6})  # rotate BNB -> ETH
    assert client.balance("BNB") == pytest.approx(0.0, abs=1e-9)
    assert rep.weights_after["ETH"] == pytest.approx(0.6, abs=0.02)


def test_min_rebal_frac_skips_dust():
    broker, client = make_broker(min_rebal_frac=0.05)
    broker.rebalance({"BNB": 0.5})
    bnb_before = client.balance("BNB")
    # a 1% target nudge is below the 5% threshold -> no swap
    rep = broker.rebalance({"BNB": 0.51})
    assert rep.n_swaps == 0
    assert client.balance("BNB") == bnb_before


def test_emergency_flatten_returns_to_usdt():
    broker, client = make_broker()
    broker.rebalance({"BNB": 0.4, "ETH": 0.2})
    assert sum(broker.positions().values()) > 0
    broker.emergency_flatten()
    assert broker.positions() == {}
    assert client.balance("USDT") == pytest.approx(broker.nav(broker.prices()), abs=1e-6)


def test_nav_conserved_minus_fees():
    broker, client = make_broker(start_usdt=1000.0)
    nav0 = broker.nav(broker.prices())
    rep = broker.rebalance({"BNB": 0.3, "ETH": 0.3})
    # NAV after = NAV before - fees paid (prices unchanged in sim)
    assert rep.nav_after == pytest.approx(nav0 - rep.fees_usd, rel=1e-6)
    assert rep.fees_usd > 0


def test_live_without_enable_flag_refuses():
    client = SimTwakClient(lambda t: PRICES[t])
    with pytest.raises(LiveTradingDisabled):
        TwakSpotBroker(client, tokens=TOKENS, live=True, live_enabled=False)


# --------------------------- Phase 1: resilience --------------------------- #
def test_rebalance_collects_failed_swaps_without_crashing():
    inner = SimTwakClient(lambda t: PRICES[t], start_usdt=1000.0)
    TwakSpotBroker(inner, tokens=TOKENS).rebalance({"BNB": 0.4, "ETH": 0.2})  # deploy first
    # now make the BNB->USDT sell fail on the next rotate
    broker = TwakSpotBroker(_FailOnPair(inner, "BNB", "USDT"), tokens=TOKENS)
    rep = broker.rebalance({"ETH": 0.6})  # wants to sell BNB (fails) + buy ETH
    assert rep.n_failed >= 1
    assert any(s.from_token == "BNB" and not s.ok for s in rep.failed_swaps)
    assert all(s.ok for s in rep.swaps if s not in rep.failed_swaps)
    # the failed sell does NOT crash the tick and does NOT corrupt the book:
    assert inner.balance("BNB") > 0  # BNB still held (sell failed)
    assert rep.nav_after > 0


def test_failed_swaps_excluded_from_n_swaps_and_fees():
    inner = SimTwakClient(lambda t: PRICES[t], start_usdt=1000.0)
    broker = TwakSpotBroker(_FailOnPair(inner, "USDT", "BNB"), tokens=TOKENS)
    rep = broker.rebalance({"BNB": 0.3, "ETH": 0.3})  # BNB buy fails, ETH buy ok
    assert rep.n_failed == 1
    assert rep.n_swaps == 1  # only the ETH buy counts
    assert all(s.ok for s in rep.swaps if s.fee_paid > 0)


def test_min_swap_usd_skips_dust_swaps():
    broker, client = make_broker(start_usdt=100.0, min_swap_usd=5.0, min_rebal_frac=0.0)
    rep = broker.rebalance({"BNB": 0.02})  # $2 notional < $5 floor
    assert rep.n_swaps == 0
    assert client.balance("BNB") == 0.0


def test_emergency_flatten_partial_failure_logged(caplog):
    # A2: a PERMANENTLY-failing leg is retried, then logged CRITICAL — never raises.
    import logging

    inner = SimTwakClient(lambda t: PRICES[t], start_usdt=1000.0)
    TwakSpotBroker(inner, tokens=TOKENS).rebalance({"BNB": 0.4, "ETH": 0.2})
    broker = TwakSpotBroker(_FailOnPair(inner, "BNB", "USDT"), tokens=TOKENS)
    with caplog.at_level(logging.CRITICAL):
        out = broker.emergency_flatten(retries=3, backoff=0)  # backoff=0 keeps the test fast
    assert any(not s.ok for s in out)  # the BNB flatten still failed after retries
    assert inner.balance("ETH") == pytest.approx(0.0, abs=1e-9)  # ETH still flattened
    assert "PARTIAL emergency flatten" in caplog.text


def test_emergency_flatten_retries_then_succeeds():
    # A2: a leg that fails once then succeeds is recovered by the retry loop.
    inner = SimTwakClient(lambda t: PRICES[t], start_usdt=1000.0)
    TwakSpotBroker(inner, tokens=TOKENS).rebalance({"BNB": 0.4})
    assert inner.balance("BNB") > 0
    flaky = _FailFirstN(inner, "BNB", "USDT", n=1)  # fail once -> the retry then succeeds
    broker = TwakSpotBroker(flaky, tokens=TOKENS)
    out = broker.emergency_flatten(backoff=0)
    assert all(s.ok for s in out)  # the failed leg was recovered
    assert flaky.calls == 1  # exactly one forced failure consumed
    assert inner.balance("BNB") == pytest.approx(0.0, abs=1e-9)  # fully flat
