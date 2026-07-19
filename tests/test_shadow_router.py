"""
Phase B — ShadowRouter tests (docs/autotrade_plan.md).

Asserts the wrapper's three guarantees:
  (a) Both legs receive the same result dict and route() is invoked on each.
  (b) A shadow-leg exception is swallowed; live route() still runs and
      its outcome is returned.
  (c) Comparison metrics fire correctly: slippage on paired placements,
      divergence on unilateral placements.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ictbot.orchestrator.router import RouteOutcome
from ictbot.orchestrator.shadow_router import ShadowRouter, _FanoutBroker

# ---- helpers ---------------------------------------------------------------


def _make_router(*, placed: bool, fill_entry: float = 0.0, side: str = "BUY"):
    """Build a MagicMock that quacks like a SignalRouter.route()."""
    router = MagicMock(name=f"router_placed={placed}")
    router.broker = MagicMock(name="broker")
    router.broker.name = f"mock-{'live' if placed else 'paper'}"
    router.broker.positions.return_value = []
    router.broker.on_bar.return_value = []
    router.broker.equity.return_value = 10_000.0
    router.cap_gate = MagicMock(name="cap_gate")

    order = MagicMock(name="order")
    order.entry = fill_entry
    order.side = side
    router.route.return_value = RouteOutcome(
        placed=placed,
        order=order if placed else None,
        rejection=None,
        reason="ok" if placed else "rejected",
    )
    return router


def _result(pair: str = "BTC/USDT:USDT", price: float = 100.0) -> dict:
    return {
        "pair": pair,
        "entry": "BUY",
        "price": price,
        "sl": 99.0,
        "tp": 103.0,
        "rr": 3.0,
        "confidence": 100,
    }


@pytest.fixture(autouse=True)
def _stub_metrics(monkeypatch):
    """Replace the shadow metrics with MagicMocks so we can assert
    label/observe/inc calls without depending on prometheus_client."""
    from ictbot.orchestrator import shadow_router as sr
    from ictbot.runtime import metrics

    slippage = MagicMock(name="shadow_fill_slippage_bps")
    slippage.labels.return_value = slippage
    diverged = MagicMock(name="shadow_diverged_total")
    diverged.labels.return_value = diverged

    monkeypatch.setattr(metrics, "shadow_fill_slippage_bps", slippage)
    monkeypatch.setattr(metrics, "shadow_diverged_total", diverged)
    # shadow_router imports the metrics module reference, so patch it
    # again at that module's resolution site:
    monkeypatch.setattr(sr.metrics, "shadow_fill_slippage_bps", slippage)
    monkeypatch.setattr(sr.metrics, "shadow_diverged_total", diverged)

    return slippage, diverged


# ---- tests -----------------------------------------------------------------


def test_both_legs_routed_with_same_result(_stub_metrics):
    live = _make_router(placed=True, fill_entry=100.0)
    shadow = _make_router(placed=True, fill_entry=100.0)
    sr = ShadowRouter(live_router=live, shadow_router=shadow)
    r = _result()

    outcome = sr.route(r)

    live.route.assert_called_once_with(r)
    shadow.route.assert_called_once_with(r)
    # Live outcome is returned (not shadow) — caller must always see
    # the real-money result.
    assert outcome is live.route.return_value


def test_shadow_exception_swallowed_live_still_runs(_stub_metrics):
    _slippage, diverged = _stub_metrics
    live = _make_router(placed=True, fill_entry=100.0)
    shadow = MagicMock(name="exploding_shadow")
    shadow.broker = MagicMock()
    shadow.broker.name = "paper"
    shadow.route.side_effect = RuntimeError("shadow boom")

    sr = ShadowRouter(live_router=live, shadow_router=shadow)
    outcome = sr.route(_result())

    # Live must still be invoked + returned.
    live.route.assert_called_once()
    assert outcome.placed is True
    # Exception counter incremented with the right reason.
    diverged.labels.assert_any_call(pair="BTC/USDT:USDT", reason="shadow_exception")
    diverged.inc.assert_called()


def test_live_exception_propagates(_stub_metrics):
    """Live exceptions are NOT swallowed — the scanner handles them."""
    live = MagicMock(name="exploding_live")
    live.broker = MagicMock()
    live.broker.name = "live"
    live.route.side_effect = RuntimeError("live boom")
    shadow = _make_router(placed=True, fill_entry=100.0)

    sr = ShadowRouter(live_router=live, shadow_router=shadow)
    with pytest.raises(RuntimeError, match="live boom"):
        sr.route(_result())
    # Shadow ran first (best-effort), so it WAS invoked before live blew up.
    shadow.route.assert_called_once()


def test_paired_placement_emits_slippage(_stub_metrics):
    slippage, diverged = _stub_metrics
    # Signal price 100; live fills at 100.05 → 5 bps worse for a BUY.
    live = _make_router(placed=True, fill_entry=100.05, side="BUY")
    shadow = _make_router(placed=True, fill_entry=100.0, side="BUY")

    sr = ShadowRouter(live_router=live, shadow_router=shadow)
    sr.route(_result(price=100.0))

    slippage.labels.assert_called_once_with(pair="BTC/USDT:USDT", side="BUY")
    # raw_bps = (100.05 - 100) / 100 * 10000 = 5.0 bps (BUY, positive)
    observed_bps = slippage.observe.call_args.args[0]
    assert observed_bps == pytest.approx(5.0, abs=1e-6)


def test_sell_slippage_sign_normalised(_stub_metrics):
    slippage, _ = _stub_metrics
    # SELL at signal 100, live fills at 100.05 → BETTER fill for a SELL,
    # so the normalised slip should be NEGATIVE (i.e. positive bps means
    # worse, negative means better).
    live = _make_router(placed=True, fill_entry=100.05, side="SELL")
    shadow = _make_router(placed=True, fill_entry=100.0, side="SELL")

    sr = ShadowRouter(live_router=live, shadow_router=shadow)
    sr.route(_result(price=100.0))

    observed_bps = slippage.observe.call_args.args[0]
    assert observed_bps == pytest.approx(-5.0, abs=1e-6)


def test_live_placed_shadow_rejected_counted(_stub_metrics):
    _, diverged = _stub_metrics
    live = _make_router(placed=True, fill_entry=100.0)
    shadow = _make_router(placed=False)

    sr = ShadowRouter(live_router=live, shadow_router=shadow)
    sr.route(_result())

    diverged.labels.assert_any_call(pair="BTC/USDT:USDT", reason="shadow_rejected")


def test_shadow_placed_live_rejected_counted(_stub_metrics):
    _, diverged = _stub_metrics
    live = _make_router(placed=False)
    shadow = _make_router(placed=True, fill_entry=100.0)

    sr = ShadowRouter(live_router=live, shadow_router=shadow)
    sr.route(_result())

    diverged.labels.assert_any_call(pair="BTC/USDT:USDT", reason="live_rejected")


def test_both_rejected_emits_no_metric(_stub_metrics):
    slippage, diverged = _stub_metrics
    live = _make_router(placed=False)
    shadow = _make_router(placed=False)

    sr = ShadowRouter(live_router=live, shadow_router=shadow)
    sr.route(_result())

    slippage.observe.assert_not_called()
    diverged.inc.assert_not_called()


# ---- fanout broker ---------------------------------------------------------


def test_fanout_on_bar_settles_both_legs():
    live = MagicMock(name="live_broker")
    live.name = "binance-live"
    live.on_bar.return_value = ["live_closed_order"]
    shadow = MagicMock(name="paper_broker")
    shadow.name = "paper"
    shadow.on_bar.return_value = ["shadow_closed_order"]

    fb = _FanoutBroker(live, shadow)
    result = fb.on_bar("BTC/USDT:USDT", {"high": 1, "low": 1})

    live.on_bar.assert_called_once_with("BTC/USDT:USDT", {"high": 1, "low": 1})
    shadow.on_bar.assert_called_once_with("BTC/USDT:USDT", {"high": 1, "low": 1})
    # Only LIVE closes are returned upstream so the scanner doesn't
    # double-log/journal shadow closes.
    assert result == ["live_closed_order"]


def test_fanout_positions_only_live():
    live = MagicMock(name="live_broker")
    live.positions.return_value = ["live_position"]
    shadow = MagicMock(name="paper_broker")
    shadow.positions.return_value = ["shadow_position"]

    fb = _FanoutBroker(live, shadow)
    assert fb.positions() == ["live_position"]
    shadow.positions.assert_not_called()


def test_fanout_shadow_exception_doesnt_break_live_settlement():
    live = MagicMock(name="live_broker")
    live.name = "live"
    live.on_bar.return_value = ["live_closed"]
    shadow = MagicMock(name="paper_broker")
    shadow.name = "paper"
    shadow.on_bar.side_effect = RuntimeError("shadow blew up")

    fb = _FanoutBroker(live, shadow)
    result = fb.on_bar("BTC/USDT:USDT", {"high": 1, "low": 1})
    # Live settlement still completes, shadow exception swallowed.
    assert result == ["live_closed"]
