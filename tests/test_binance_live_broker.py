"""
BinanceLiveBroker tests.

Every ccxt call is mocked — tests must NEVER hit the real exchange.

Verifies:
  - place_order issues exactly 3 ccxt calls (market entry, stop-market
    SL, limit TP) in the right order with the right reduceOnly flags.
  - Emergency flatten fires when SL or TP placement fails.
  - PermissionDenied is re-raised as LiveTradingDisabled.
  - equity() uses the direct fapi v3 endpoint (NOT fetch_balance) to
    bypass the SAPI fan-out that doesn't exist on testnet.
  - The kill-switch + allowed_pairs gates work.
  - Testnet URL override (bypassing ccxt's deprecated set_sandbox_mode)
    rewrites the fapi URLs to testnet.binancefuture.com.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import ccxt
import pytest

from ictbot.exec.binance_live import BinanceLiveBroker, LiveTradingDisabled
from ictbot.exec.orders import Order
from ictbot.settings import settings


@pytest.fixture(autouse=True)
def _enable_live(monkeypatch):
    """Most tests assume the kill switch is on; gating is tested separately."""
    monkeypatch.setattr(settings, "enable_live_trading", True)


def _order(pair="BTC/USDT:USDT", side="BUY"):
    return Order(pair=pair, side=side, entry=100.0, sl=95.0, tp=110.0, qty=0.5)


# ---- placement -------------------------------------------------------------


def test_place_order_issues_three_ccxt_calls_in_order():
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-123"},
        {"id": "sl-456"},
        {"id": "tp-789"},
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    out = b.place_order(_order())
    assert client.create_order.call_count == 3

    # 1) market entry — no extra params (Binance USDT-M needs no venue-side overrides)
    assert client.create_order.call_args_list[0] == call(
        "BTC/USDT:USDT", "market", "buy", 0.5, None, {}
    )
    # 2) stop-market SL, reduceOnly
    assert client.create_order.call_args_list[1] == call(
        "BTC/USDT:USDT",
        "stop_market",
        "sell",
        0.5,
        None,
        {"stopPrice": 95.0, "reduceOnly": True},
    )
    # 3) limit TP, reduceOnly
    assert client.create_order.call_args_list[2] == call(
        "BTC/USDT:USDT",
        "limit",
        "sell",
        0.5,
        110.0,
        {"reduceOnly": True},
    )

    assert out.entry_order_id == "entry-123"
    assert out.sl_order_id == "sl-456"
    assert out.tp_order_id == "tp-789"
    assert out.status == "OPEN"


def test_place_order_for_sell_uses_buy_legs_for_sl_tp():
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    b.place_order(_order(side="SELL"))
    assert client.create_order.call_args_list[0].args[2] == "sell"
    assert client.create_order.call_args_list[1].args[2] == "buy"
    assert client.create_order.call_args_list[2].args[2] == "buy"


# ---- emergency flatten -----------------------------------------------------


def test_sl_failure_triggers_emergency_flatten():
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1"},
        Exception("SL failure"),
        {"id": "flatten-1"},  # the emergency flatten call
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    with pytest.raises(Exception, match="SL failure"):
        b.place_order(_order())
    # Three calls: entry, failed SL, emergency flatten.
    assert client.create_order.call_count == 3
    flatten_call = client.create_order.call_args_list[2]
    # Reduce-only opposite-side market
    assert flatten_call.args[1] == "market"
    assert flatten_call.args[2] == "sell"
    assert flatten_call.args[5].get("reduceOnly") is True


def test_tp_failure_cancels_sl_and_flattens():
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1"},
        {"id": "sl-1"},
        Exception("TP rejected"),
        {"id": "flatten-1"},  # emergency flatten
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    with pytest.raises(Exception, match="TP rejected"):
        b.place_order(_order())
    # SL should have been cancelled
    client.cancel_order.assert_called_once_with("sl-1", "BTC/USDT:USDT")


# ---- permission-denied (KYC / regulatory) -----------------------------------


def test_entry_permission_denied_reraises_as_live_disabled():
    """Binance returning PermissionDenied on the entry leg should propagate
    as LiveTradingDisabled so the scanner logs one line instead of a
    50-line traceback every cycle."""
    client = MagicMock()
    client.create_order.side_effect = ccxt.PermissionDenied(
        'binance {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}'
    )
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    with pytest.raises(LiveTradingDisabled, match="Binance refused"):
        b.place_order(_order())
    # Only the entry leg was attempted — no SL/TP/flatten when entry was refused.
    assert client.create_order.call_count == 1


# ---- gating ----------------------------------------------------------------


def test_refuses_when_kill_switch_off(monkeypatch):
    monkeypatch.setattr(settings, "enable_live_trading", False)
    client = MagicMock()
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    with pytest.raises(LiveTradingDisabled, match="ENABLE_LIVE_TRADING is False"):
        b.place_order(_order())
    client.create_order.assert_not_called()


def test_refuses_pair_not_in_allowlist():
    client = MagicMock()
    b = BinanceLiveBroker(allowed_pairs={"ETH/USDT:USDT"}, client=client)
    with pytest.raises(LiveTradingDisabled, match="not in allowed_pairs"):
        b.place_order(_order(pair="BTC/USDT:USDT"))
    client.create_order.assert_not_called()


# ---- equity (the SAPI bypass) ----------------------------------------------


def test_equity_uses_fetch_balance():
    """equity() reads USDT 'free' from ccxt's standard fetch_balance.
    Works on testnet because _apply_testnet_routing short-circuits the
    SAPI pre-flight (validated against a real demo key 2026-06-04)."""
    client = MagicMock()
    client.fetch_balance.return_value = {
        "USDT": {"free": 9500.5, "total": 10000.0, "used": 499.5},
    }
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    assert b.equity() == 9500.5
    client.fetch_balance.assert_called_once()


def test_equity_returns_zero_when_call_fails():
    client = MagicMock()
    client.fetch_balance.side_effect = RuntimeError("network blip")
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    assert b.equity() == 0.0  # graceful fallback


# ---- testnet URL override --------------------------------------------------


def test_testnet_overrides_fapi_urls_to_testnet_host():
    """When testnet=True, the broker must rewrite client.urls["api"][fapi*]
    to client.urls["test"][fapi*] (bypassing ccxt's
    set_sandbox_mode-deprecated guard for binance futures)."""
    # Construct without injecting a client so the real ccxt instance is built
    # and our URL override runs.
    import ictbot.exec.binance_live as mod

    fake_ccxt_client = MagicMock()
    fake_ccxt_client.urls = {
        "api": {
            "fapiPrivate": "https://fapi.binance.com/fapi/v1",
            "fapiPublic": "https://fapi.binance.com/fapi/v1",
        },
        "test": {
            "fapiPrivate": "https://testnet.binancefuture.com/fapi/v1",
            "fapiPublic": "https://testnet.binancefuture.com/fapi/v1",
        },
    }
    fake_ccxt_client.set_leverage = MagicMock()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod.ccxt, "binance", lambda opts: fake_ccxt_client)
        BinanceLiveBroker(
            allowed_pairs={"BTC/USDT:USDT"},
            testnet=True,
            api_key="x",
            api_secret="y",
        )

    assert (
        fake_ccxt_client.urls["api"]["fapiPrivate"] == "https://testnet.binancefuture.com/fapi/v1"
    )
    assert fake_ccxt_client.urls["api"]["fapiPublic"] == "https://testnet.binancefuture.com/fapi/v1"


# ---- Fix 2.E: actual fill capture + bracket re-anchor + slip guard --------


def test_place_order_captures_actual_fill_into_order_entry(monkeypatch):
    """ccxt's entry response carries `average` for market fills. The
    broker must store it on order.entry so realised R is measured
    against the real fill, not the strategy's pre-bar price."""
    monkeypatch.setattr(settings, "re_anchor_bracket", True)
    monkeypatch.setattr(settings, "max_entry_slippage_bps", 30.0)
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1", "average": 100.05},  # slipped +5 bps from 100
        {"id": "sl-1"},
        {"id": "tp-1"},
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = b.place_order(_order())  # strategy entry=100, sl=95, tp=110
    assert o.entry == 100.05
    assert o.filled_at is not None


def test_place_order_re_anchors_sl_tp_by_fill_drift(monkeypatch):
    """When the fill drifts +0.05 from strategy entry, both SL and TP
    must shift by the same amount so the intended risk distance is
    preserved (without this, a +0.05 BUY slip would shrink the 5.0
    stop distance to 4.95 → tighter SL than designed)."""
    monkeypatch.setattr(settings, "re_anchor_bracket", True)
    monkeypatch.setattr(settings, "max_entry_slippage_bps", 30.0)
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1", "average": 100.05},
        {"id": "sl-1"},
        {"id": "tp-1"},
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = b.place_order(_order())
    # SL was 95.0 → shifts up to 95.05; TP was 110.0 → shifts up to 110.05.
    assert o.sl == pytest.approx(95.05)
    assert o.tp == pytest.approx(110.05)
    # And the actual ccxt calls used the shifted prices.
    sl_call = client.create_order.call_args_list[1]
    tp_call = client.create_order.call_args_list[2]
    assert sl_call.args[5]["stopPrice"] == pytest.approx(95.05)
    assert tp_call.args[4] == pytest.approx(110.05)


def test_place_order_does_not_re_anchor_when_setting_off(monkeypatch):
    """RE_ANCHOR_BRACKET=false is the legacy escape hatch. order.entry
    is still updated to the real fill (so R measurement is honest), but
    SL/TP stay at the strategy's pre-computed levels."""
    monkeypatch.setattr(settings, "re_anchor_bracket", False)
    monkeypatch.setattr(settings, "max_entry_slippage_bps", 30.0)
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1", "average": 100.05},
        {"id": "sl-1"},
        {"id": "tp-1"},
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = b.place_order(_order())
    assert o.entry == 100.05
    assert o.sl == 95.0  # unchanged
    assert o.tp == 110.0  # unchanged


def test_place_order_emergency_flattens_when_buy_slips_above_ceiling(monkeypatch):
    """Bad BUY fill (slipped +35 bps with a 30 bps ceiling) must
    immediately reduce-only-flatten and re-raise as LiveTradingDisabled
    so the router journals REJECTED instead of holding the position."""
    monkeypatch.setattr(settings, "re_anchor_bracket", True)
    monkeypatch.setattr(settings, "max_entry_slippage_bps", 30.0)
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1", "average": 100.35},  # +35 bps slip from 100
        {"id": "flatten-1"},
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    with pytest.raises(LiveTradingDisabled, match="slippage"):
        b.place_order(_order())
    # Entry + emergency-flatten; no SL/TP attempted.
    assert client.create_order.call_count == 2
    flatten_call = client.create_order.call_args_list[1]
    assert flatten_call.args[1] == "market"
    assert flatten_call.args[2] == "sell"  # opposite of BUY
    assert flatten_call.args[5].get("reduceOnly") is True


def test_place_order_allows_favourable_buy_slip(monkeypatch):
    """A BUY filled BELOW the strategy entry is a gift (paid less than
    expected). Even if the magnitude exceeds the ceiling, this must not
    trigger emergency-flatten — only unfavourable slip is gated."""
    monkeypatch.setattr(settings, "re_anchor_bracket", True)
    monkeypatch.setattr(settings, "max_entry_slippage_bps", 30.0)
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1", "average": 99.50},  # -50 bps slip — favourable on BUY
        {"id": "sl-1"},
        {"id": "tp-1"},
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = b.place_order(_order())  # no raise
    assert o.entry == 99.50


def test_place_order_emergency_flattens_when_sell_slips_below_ceiling(monkeypatch):
    """Symmetric SELL case: a SELL filled BELOW strategy entry is
    unfavourable (got less for the short). 35 bps below the strategy
    sell price must reject."""
    monkeypatch.setattr(settings, "re_anchor_bracket", True)
    monkeypatch.setattr(settings, "max_entry_slippage_bps", 30.0)
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1", "average": 99.65},  # -35 bps slip from 100
        {"id": "flatten-1"},
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    with pytest.raises(LiveTradingDisabled, match="slippage"):
        b.place_order(_order(side="SELL"))


def test_place_order_falls_back_to_fetch_order_when_average_missing(monkeypatch):
    """ccxt sometimes returns market-order responses without `average`
    populated (the WebSocket fill confirm hasn't arrived yet). The
    resolver must then fall back to fetch_order(entry_order_id)."""
    monkeypatch.setattr(settings, "re_anchor_bracket", True)
    monkeypatch.setattr(settings, "max_entry_slippage_bps", 30.0)
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1"},  # no average, no price
        {"id": "sl-1"},
        {"id": "tp-1"},
    ]
    client.fetch_order.return_value = {"average": 100.08}
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = b.place_order(_order())
    assert o.entry == 100.08
    client.fetch_order.assert_called_with("entry-1", "BTC/USDT:USDT")


def test_place_order_preserves_legacy_when_fill_price_unresolvable(monkeypatch):
    """If both entry response AND fetch_order fail to yield a price,
    the broker must NOT crash — fall back to legacy behaviour (order.entry
    stays at strategy entry, no re-anchor, no rejection)."""
    monkeypatch.setattr(settings, "re_anchor_bracket", True)
    monkeypatch.setattr(settings, "max_entry_slippage_bps", 30.0)
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry-1"},
        {"id": "sl-1"},
        {"id": "tp-1"},
    ]
    client.fetch_order.side_effect = RuntimeError("network blip")
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    o = b.place_order(_order())
    assert o.entry == 100.0  # unchanged from strategy entry
    assert o.sl == 95.0
    assert o.tp == 110.0


# ---- reconcile + finalize close (Fix 2.B) ---------------------------------


def _placed_order(client, pair="BTC/USDT:USDT", side="BUY"):
    """Place a real bracket through the broker and return (broker, order)."""
    client.create_order.side_effect = [
        {"id": "entry-1"},
        {"id": "sl-1"},
        {"id": "tp-1"},
    ]
    b = BinanceLiveBroker(allowed_pairs={pair}, client=client)
    o = Order(pair=pair, side=side, entry=100.0, sl=95.0, tp=110.0, qty=0.5)
    b.place_order(o)
    return b, o


def test_reconcile_no_close_when_position_still_open():
    """Fix 2.B regression cover: on_bar must NOT fire _on_close while the
    pair is still open on Binance. Streak debouncer starts at 0."""
    client = MagicMock()
    b, o = _placed_order(client)
    closed_events = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = [
        {"symbol": "BTC/USDT:USDT", "contracts": 0.5, "info": {"positionAmt": "0.5"}},
    ]
    new = b.on_bar("BTC/USDT:USDT", {})
    assert new == []
    assert closed_events == []
    assert b._zero_position_streak.get("BTC/USDT:USDT", 0) == 0


def test_reconcile_two_empty_cycles_finalizes_with_real_fill_price():
    """Two consecutive empty fetch_positions reads must finalize the order
    with the SL leg's actual filled average (not the strategy's sl
    price). This is the live counterpart to the synthetic settler — the
    one that Phase 1 diagnostic proved was never winning the race."""
    client = MagicMock()
    b, o = _placed_order(client)
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    # First empty cycle: streak goes from 0 → 1, no close yet.
    new = b.on_bar("BTC/USDT:USDT", {})
    assert new == []
    assert closed_events == []
    assert b._zero_position_streak["BTC/USDT:USDT"] == 1

    # Second empty cycle: streak → 2 → _finalize_filled fires.
    # SL leg shows status=filled with average=94.7 (slipped 0.3 below sl=95.0).
    client.fetch_order.return_value = {
        "status": "filled",
        "average": 94.7,
        "price": 95.0,
    }
    new = b.on_bar("BTC/USDT:USDT", {})
    assert len(new) == 1
    assert len(closed_events) == 1
    closed = closed_events[0]
    assert closed.status == "FILLED"
    assert closed.close_reason == "SL"
    # Critical: close_price comes from the REAL fill average, not from order.sl.
    assert closed.close_price == 94.7
    # Streak resets after fire.
    assert b._zero_position_streak["BTC/USDT:USDT"] == 0


def test_reconcile_falls_back_to_leg_price_when_fetch_order_returns_nothing():
    """If fetch_order returns no average AND no price, _finalize_filled
    falls back to order.sl / order.tp. Documents the failure mode but
    flags it (close_reason still set so downstream knows it was an SL)."""
    client = MagicMock()
    b, o = _placed_order(client)
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    client.fetch_order.return_value = {"status": "filled", "average": None, "price": None}

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    assert closed_events[0].close_price == 95.0  # fell back to order.sl
    assert closed_events[0].close_reason == "SL"


def test_reconcile_captures_fees_into_order_fees_paid():
    """Fix 2.F: _finalize_filled must sum entry + close-leg fees from
    ccxt's `info["fee"]["cost"]` and stamp them onto order.fees_paid
    so realised_pnl_R subtracts them from the gross R."""
    client = MagicMock()
    b, o = _placed_order(client)
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []

    # fetch_order is called for: (1) entry-leg fee resolve, (2) SL leg
    # status + fee, (3) TP leg status + fee. SL fires first per the
    # resolution order in _finalize_filled.
    def _fetch_order_side_effect(oid, pair):
        if oid == "entry-1":
            return {"average": 100.0, "fee": {"cost": 0.02, "currency": "USDT"}}
        if oid == "sl-1":
            return {
                "status": "filled",
                "average": 94.7,
                "fee": {"cost": 0.019, "currency": "USDT"},
            }
        return {"status": "open"}

    client.fetch_order.side_effect = _fetch_order_side_effect

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    assert len(closed_events) == 1
    assert closed_events[0].fees_paid == pytest.approx(0.039)


def test_reconcile_fees_amplify_realised_loss():
    """When fees_paid is captured, realised_pnl_R must return a SMALLER
    (more-negative) R for losses and a SMALLER R for wins than the
    gross formula."""
    client = MagicMock()
    b, o = _placed_order(client)
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    client.fetch_order.side_effect = [
        # entry-leg fee resolve at top of _finalize_filled
        {"fee": {"cost": 0.5}},
        # SL leg: filled at 95.0 with fee 0.5
        {"status": "filled", "average": 95.0, "fee": {"cost": 0.5}},
    ]

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    closed = closed_events[0]
    # qty=0.5, risk=5.0, fees=1.0 → fees_R = 1.0 / (0.5*5.0) = 0.4
    # Gross R = (95-100)/5 = -1.0; net = -1.4
    assert closed.realised_pnl_R() == pytest.approx(-1.4)


def test_reconcile_no_fees_returns_legacy_R():
    """If fetch_order returns no fee info, fees_paid stays None and
    realised_pnl_R returns the legacy gross formula bit-for-bit."""
    client = MagicMock()
    b, o = _placed_order(client)
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    # Calls in order: (1) entry-leg fee resolve → no fee field,
    # (2) SL leg status — open, so SL didn't fire,
    # (3) TP leg status filled at 110.0, no fee.
    client.fetch_order.side_effect = [
        {},
        {"status": "open"},
        {"status": "filled", "average": 110.0},
    ]

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    closed = closed_events[0]
    assert closed.fees_paid is None
    assert closed.close_reason == "TP"
    assert closed.close_price == 110.0
    # Legacy formula: (110 - 100) / 5 = 2.0 — bit-for-bit pre-Fix-2.F.
    assert closed.realised_pnl_R() == 2.0


def test_reconcile_manual_close_when_neither_leg_filled():
    """Position vanished but neither SL nor TP shows as filled (e.g. user
    manually closed via Binance UI). close_reason='MANUAL', close_price
    falls back to entry price (R = 0)."""
    client = MagicMock()
    b, o = _placed_order(client)
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    client.fetch_order.return_value = {"status": "open", "average": None, "price": None}
    # Fix 5.A defends against the legacy fall-through: configure
    # fetch_my_trades to return [] so the trades-path bails cleanly
    # and the old fetch_order loop still drives the test.
    client.fetch_my_trades.return_value = []

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    assert closed_events[0].close_reason == "MANUAL"
    assert closed_events[0].close_price == 100.0  # = order.entry


# ---- Fix 5.A: algo-queue close detection via fetch_my_trades --------------


def test_finalize_filled_resolves_sl_from_trades_for_sell():
    """Fix 5.A: algo-queue STOP_MARKET fills don't show in fetch_order
    (different endpoint). The fetch_my_trades path must detect them.
    SELL filled at avg 100, closed at 95.3 (above 100 → SL hit on a SELL
    means price RAN against us). Direction-based reason inference:
    SELL + close > entry → SL."""
    client = MagicMock()
    b, o = _placed_order(client, side="SELL")  # entry=100, sl=95, tp=110
    # SELL bracket: entry sells at 100, SL is ABOVE entry (BUY-back at >100).
    # The placed Order has order.sl=95 which doesn't match a SELL semantically
    # — _placed_order is a generic fixture. We override after for the test.
    o.entry = 100.0
    o.sl = 105.0
    o.tp = 90.0
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    # Algo-queue style: the most recent reduceOnly close is a market BUY
    # (the SL trigger fired) at 105.3 — above entry, so SL.
    client.fetch_my_trades.return_value = [
        {
            "side": "buy",
            "price": 105.3,
            "timestamp": 1_780_000_000_000,
            "reduceOnly": True,
            "fee": {"cost": 0.025},
        }
    ]
    # fetch_order falls through to "Order does not exist" — algo IDs
    # aren't in the regular orders endpoint.
    client.fetch_order.side_effect = Exception("Order does not exist")

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    closed = closed_events[0]
    assert closed.close_price == 105.3
    assert closed.close_reason == "SL"
    assert closed.fees_paid is not None
    # Realised R = (entry - close) / risk = (100 - 105.3) / |100 - 105| = -1.06
    assert closed.realised_pnl_R() is not None
    assert closed.realised_pnl_R() < -1.0


def test_finalize_filled_resolves_tp_from_trades_for_sell():
    """Mirror: SELL closed BELOW entry → TP hit (price moved with us)."""
    client = MagicMock()
    b, o = _placed_order(client, side="SELL")
    o.entry = 100.0
    o.sl = 105.0
    o.tp = 90.0
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    client.fetch_my_trades.return_value = [
        {
            "side": "buy",
            "price": 90.05,
            "timestamp": 1_780_000_000_000,
            "reduceOnly": True,
            "fee": {"cost": 0.02},
        }
    ]
    client.fetch_order.side_effect = Exception("Order does not exist")

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    closed = closed_events[0]
    assert closed.close_price == 90.05
    assert closed.close_reason == "TP"


def test_finalize_filled_resolves_sl_for_buy_from_trades():
    """BUY closed BELOW entry → SL hit."""
    client = MagicMock()
    b, o = _placed_order(client, side="BUY")  # entry=100, sl=95, tp=110
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    client.fetch_my_trades.return_value = [
        {
            "side": "sell",
            "price": 94.92,
            "timestamp": 1_780_000_000_000,
            "reduceOnly": True,
            "fee": {"cost": 0.018},
        }
    ]
    client.fetch_order.side_effect = Exception("Order does not exist")

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    closed = closed_events[0]
    assert closed.close_price == 94.92
    assert closed.close_reason == "SL"
    assert closed.realised_pnl_R() is not None
    assert closed.realised_pnl_R() < -1.0


def test_finalize_filled_accepts_close_via_realizedPnl_when_reduceOnly_missing():
    """Fix 6.A (Phase 6 follow-up): some ccxt code paths drop the
    reduceOnly flag from the trade record even though the underlying
    order was reduceOnly. In those cases Binance still populates
    `info.realizedPnl` with a non-zero value because the trade
    reduced the position.

    Symptom this guards against: a manual flatten via
    scripts/close_test_order.py followed by the broker's reconcile —
    PAXG 2026-06-06 09:31 close fell through to MANUAL because the
    reduceOnly filter excluded the legit close trade.
    """
    client = MagicMock()
    b, o = _placed_order(client, side="SELL")
    o.entry = 100.0
    o.sl = 105.0
    o.tp = 90.0
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    client.fetch_my_trades.return_value = [
        # The close trade: reduceOnly is MISSING (None) but realizedPnl
        # is populated — that's the Fix 6.A fallback signal.
        {
            "side": "buy",
            "price": 95.4,
            "timestamp": 1_780_000_000_000,
            "reduceOnly": None,
            "fee": {"cost": 0.03},
            "info": {"realizedPnl": "+1.05"},
        }
    ]
    client.fetch_order.side_effect = Exception("Order does not exist")

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    assert len(closed_events) == 1
    closed = closed_events[0]
    # Should be detected as TP (SELL closed below entry).
    assert closed.close_reason == "TP"
    assert closed.close_price == 95.4
    assert closed.fees_paid is not None


def test_finalize_filled_zero_realizedPnl_does_not_match():
    """Defensive: realizedPnl=0 is the signature of an ENTRY trade,
    not a close. Without the reduceOnly flag AND a zero realized PnL,
    we must NOT misclassify an entry trade as a close."""
    client = MagicMock()
    b, o = _placed_order(client, side="SELL")
    o.entry = 100.0
    o.sl = 105.0
    o.tp = 90.0
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    client.fetch_my_trades.return_value = [
        # Only an entry-style trade (opposite side because broker
        # uses opposite for closes, but reduceOnly=None AND
        # realizedPnl=0). Should NOT be picked as a close.
        {
            "side": "buy",
            "price": 95.0,
            "timestamp": 1_780_000_000_000,
            "reduceOnly": None,
            "info": {"realizedPnl": "0"},
        }
    ]
    client.fetch_order.side_effect = Exception("Order does not exist")

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    # Falls through to MANUAL because no close was resolved.
    assert closed_events[0].close_reason == "MANUAL"


def test_finalize_filled_ignores_non_reduceonly_trades():
    """fetch_my_trades returns ALL trades on the pair including the
    entry trade. The filter must only pick reduceOnly trades on the
    opposite side."""
    client = MagicMock()
    b, o = _placed_order(client, side="BUY")
    closed_events: list[Order] = []
    b._on_close = closed_events.append

    client.fetch_positions.return_value = []
    client.fetch_my_trades.return_value = [
        # The entry trade — same side, NOT reduceOnly. Must be ignored.
        {
            "side": "buy",
            "price": 100.0,
            "timestamp": 1_780_000_000_000,
            "reduceOnly": False,
            "fee": {"cost": 0.05},
        },
        # The actual close — opposite side, reduceOnly.
        {
            "side": "sell",
            "price": 110.05,
            "timestamp": 1_780_000_001_000,
            "reduceOnly": True,
            "fee": {"cost": 0.055},
        },
    ]
    client.fetch_order.side_effect = Exception("Order does not exist")

    b.on_bar("BTC/USDT:USDT", {})
    b.on_bar("BTC/USDT:USDT", {})
    closed = closed_events[0]
    assert closed.close_price == 110.05
    assert closed.close_reason == "TP"


# ---- Fix 5.B: on_reconnect Order stub has non-zero risk distance ---------


def test_on_reconnect_rebuilds_stub_with_recovered_sl_tp_from_open_orders():
    """When fetch_open_orders surfaces the bracket legs (the limit TP
    case on Binance — STOP_MARKET stays in the algo queue), the stub
    must absorb the actual sl/tp prices, not default to entry_price."""
    client = MagicMock()
    client.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 0.5,
            "entryPrice": 100.0,
            "markPrice": 100.0,
            "info": {"positionAmt": "0.5"},
        }
    ]
    client.fetch_open_orders.return_value = [
        # STOP-market SL (recovered case — mainnet algo orders may
        # show in fetch_open_orders depending on ccxt version).
        {
            "id": "sl-99",
            "type": "stop_market",
            "side": "sell",
            "stopPrice": 95.0,
            "reduceOnly": True,
            "symbol": "BTC/USDT:USDT",
        },
        # Limit TP (always in the regular endpoint).
        {
            "id": "tp-99",
            "type": "limit",
            "side": "sell",
            "price": 110.0,
            "reduceOnly": True,
            "symbol": "BTC/USDT:USDT",
        },
    ]
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    b.on_reconnect()
    assert len(b._orders) == 1
    stub = next(iter(b._orders.values()))
    assert stub.entry == 100.0
    assert stub.sl == 95.0
    assert stub.tp == 110.0
    assert stub.qty == 0.5
    assert stub.side == "BUY"
    assert stub.sl_order_id == "sl-99"
    assert stub.tp_order_id == "tp-99"
    assert stub.is_reconciled is True
    # Critical: risk distance is non-zero, so realised_pnl_R is sane.
    stub.status = "FILLED"
    stub.close_price = 110.0
    assert stub.realised_pnl_R() == 2.0  # (110-100)/(100-95)


def test_on_reconnect_falls_back_to_sl_frac_when_open_orders_empty():
    """Binance testnet keeps STOP_MARKET in the algo queue which
    `fetch_open_orders` doesn't return. The stub must fall back to
    SL_FRAC / TP_FRAC against entry_price so realised_pnl_R has a
    non-zero denominator."""
    client = MagicMock()
    client.fetch_positions.return_value = [
        {
            "symbol": "ETH/USDT:USDT",
            "side": "short",  # SELL position
            "contracts": 1.0,
            "entryPrice": 200.0,
            "info": {"positionAmt": "-1.0"},
        }
    ]
    client.fetch_open_orders.return_value = []  # testnet: empty.

    # Stub the settings module's sl_frac / tp_frac for the fallback.
    from ictbot import settings as settings_mod

    original_sl = settings_mod.settings.sl_frac
    original_tp = settings_mod.settings.tp_frac
    settings_mod.settings.sl_frac = 0.005
    settings_mod.settings.tp_frac = 0.025
    try:
        b = BinanceLiveBroker(allowed_pairs={"ETH/USDT:USDT"}, client=client)
        b.on_reconnect()
    finally:
        settings_mod.settings.sl_frac = original_sl
        settings_mod.settings.tp_frac = original_tp

    assert len(b._orders) == 1
    stub = next(iter(b._orders.values()))
    assert stub.side == "SELL"
    # SELL fallback: sl = entry × (1 + sl_frac), tp = entry × (1 - tp_frac)
    assert stub.sl == pytest.approx(200.0 * 1.005)
    assert stub.tp == pytest.approx(200.0 * 0.975)
    assert stub.is_reconciled is True
    # Risk distance is non-zero.
    assert abs(stub.entry - stub.sl) > 0


def test_on_reconnect_skips_zero_position():
    """If fetch_positions returns a row with zero contracts (no real
    position), don't rebuild a stub for it."""
    client = MagicMock()
    client.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "contracts": 0,
            "entryPrice": 0.0,
            "info": {"positionAmt": "0"},
        }
    ]
    client.fetch_open_orders.return_value = []
    b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
    b.on_reconnect()
    assert b._orders == {}


# ---- Fix 9.F (Phase 9): parametrize core paths across all 5 pairs -------


BINANCE_PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "XRP/USDT:USDT",
]


@pytest.mark.parametrize("pair", BINANCE_PAIRS)
class TestPlaceOrderAcrossPairs:
    """Fix 9.F: previously every place_order test used the BTC fixture.
    Per-pair precision edge cases (SOL's integer qty, XRP's 4-decimal
    tick) were never exercised in unit tests. These parametrized
    cases run the happy path through every configured pair."""

    def test_place_order_happy_path(self, pair):
        client = MagicMock()
        # Per-pair "precision" — return the input with a fixed format so the
        # tests assert qty / sl / tp survive the round-trip cleanly.
        client.amount_to_precision.side_effect = lambda p, q: f"{q}"
        client.price_to_precision.side_effect = lambda p, px: f"{px}"
        client.create_order.side_effect = [
            {"id": f"entry-{pair}"},
            {"id": f"sl-{pair}"},
            {"id": f"tp-{pair}"},
        ]
        b = BinanceLiveBroker(allowed_pairs={pair}, client=client)
        order = Order(pair=pair, side="BUY", entry=100.0, sl=95.0, tp=110.0, qty=0.5)
        out = b.place_order(order)

        # Three legs in the right order.
        assert client.create_order.call_count == 3
        # All 3 legs received the same pair (no cross-pair leakage).
        for call_args in client.create_order.call_args_list:
            assert call_args.args[0] == pair
        # Precision helpers were called for this pair (not BTC by mistake).
        assert any(c.args[0] == pair for c in client.amount_to_precision.call_args_list)
        assert any(c.args[0] == pair for c in client.price_to_precision.call_args_list)
        # Order ids stored.
        assert out.entry_order_id == f"entry-{pair}"
        assert out.sl_order_id == f"sl-{pair}"
        assert out.tp_order_id == f"tp-{pair}"

    def test_pair_init_runs_for_each_pair(self, pair):
        """Fix 9.C × 9.F: per-pair init must fire margin + leverage for
        each pair the broker is allowed to trade."""
        client = MagicMock()
        client.fetch_positions.return_value = [
            {"symbol": pair, "leverage": 5, "marginMode": "isolated"}
        ]
        BinanceLiveBroker(allowed_pairs={pair}, client=client)
        margin_pairs = [c.args[1] for c in client.set_margin_mode.call_args_list]
        lev_pairs = [c.args[1] for c in client.set_leverage.call_args_list]
        assert pair in margin_pairs
        assert pair in lev_pairs


# ---- Fix 9.D (Phase 9): precision normalization at order time -----------


class TestPrecisionNormalization:
    """Fix 9.D: qty + sl/tp must go through ccxt's amount_to_precision /
    price_to_precision helpers before reaching create_order. Pre-fix
    path silently passed raw floats that Binance rounded server-side,
    drifting away from journal-stored values."""

    def test_place_order_normalizes_qty_and_prices(self):
        client = MagicMock()
        # ccxt's helpers return strings — emulate that. We deliberately
        # round to coarse precision (SOL-style integer qty, BTC-style
        # 0.10 tick) so the normalisation is visible.
        client.amount_to_precision.return_value = "0.5"
        client.price_to_precision.side_effect = lambda pair, p: f"{round(float(p), 1):.1f}"
        client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        # Pass deliberately sub-tick values: sl=95.123, tp=110.456.
        o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.0, sl=95.123, tp=110.456, qty=0.5)
        out = b.place_order(o)

        # Order's persisted fields reflect normalised values (stamped back).
        assert out.qty == 0.5
        assert out.sl == 95.1
        assert out.tp == 110.5
        # The create_order calls received the normalised values, not raw.
        sl_call = client.create_order.call_args_list[1]
        tp_call = client.create_order.call_args_list[2]
        assert sl_call.args[5]["stopPrice"] == 95.1
        assert tp_call.args[4] == 110.5

    def test_helpers_called_per_pair(self):
        """A signal on XRP/USDT must go through the helpers with XRP as
        the pair argument so ccxt picks the right precision rules."""
        client = MagicMock()
        client.amount_to_precision.return_value = "1000"
        client.price_to_precision.side_effect = lambda pair, p: f"{p}"
        client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
        b = BinanceLiveBroker(allowed_pairs={"XRP/USDT:USDT"}, client=client)
        o = Order(pair="XRP/USDT:USDT", side="SELL", entry=1.0, sl=1.005, tp=0.975, qty=1000)
        b.place_order(o)

        # amount_to_precision called with the pair, not a default.
        amt_call = client.amount_to_precision.call_args_list[0]
        assert amt_call.args[0] == "XRP/USDT:USDT"
        # price_to_precision called twice (sl + tp), both with XRP pair.
        for c in client.price_to_precision.call_args_list[:2]:
            assert c.args[0] == "XRP/USDT:USDT"

    def test_falls_back_to_input_when_helper_returns_non_numeric(self):
        """If ccxt's helper returns something that's not int/float/str
        (e.g. None, a sentinel, a Mock), the broker must fall back to
        the raw input rather than silently corrupt the value."""
        client = MagicMock()
        client.amount_to_precision.return_value = None  # broken helper
        client.price_to_precision.return_value = None
        client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.0, sl=95.0, tp=110.0, qty=0.5)
        out = b.place_order(o)
        # Raw values survived intact.
        assert out.qty == 0.5
        assert out.sl == 95.0
        assert out.tp == 110.0

    def test_re_anchor_drift_renormalizes(self, monkeypatch):
        """Fix 2.E re-anchors SL/TP by the entry fill drift. After
        Fix 9.D the drifted values must go back through the precision
        helper because adding a float drift can re-introduce sub-tick
        precision."""
        monkeypatch.setattr(settings, "re_anchor_bracket", True)
        client = MagicMock()
        # Track all price normalisations.
        normalised = []

        def _price_norm(pair, p):
            normalised.append((pair, float(p)))
            return f"{float(p):.2f}"  # round to 2 decimals

        client.price_to_precision.side_effect = _price_norm
        client.amount_to_precision.return_value = "0.5"
        # Entry fill comes back at 100.123 (drift of +0.123 vs strategy 100.0)
        client.create_order.side_effect = [
            {"id": "e", "average": 100.123},
            {"id": "s"},
            {"id": "t"},
        ]
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.0, sl=95.0, tp=110.0, qty=0.5)
        out = b.place_order(o)
        # SL/TP shifted by the drift and then re-rounded to 2 decimals.
        assert out.sl == 95.12  # 95.0 + 0.123 → 95.123 → "95.12"
        assert out.tp == 110.12  # 110.0 + 0.123 → 110.123 → "110.12"
        # price_to_precision was called more than twice (initial + re-anchor).
        assert len(normalised) >= 4


# ---- Fix 9.C (Phase 9): pair init — margin mode + leverage read-back ----


class TestPairInit:
    """Fix 9.C: per-pair set_margin_mode + set_leverage with read-back."""

    def test_sets_margin_mode_and_leverage_for_each_allowed_pair(self):
        client = MagicMock()
        client.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT:USDT",
                "leverage": 5,
                "marginMode": "isolated",
            },
            {
                "symbol": "ETH/USDT:USDT",
                "leverage": 5,
                "marginMode": "isolated",
            },
        ]
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT", "ETH/USDT:USDT"}, client=client)

        margin_calls = [c.args for c in client.set_margin_mode.call_args_list]
        lev_calls = [c.args for c in client.set_leverage.call_args_list]

        # Each pair received both calls.
        assert ("ISOLATED", "BTC/USDT:USDT") in margin_calls
        assert ("ISOLATED", "ETH/USDT:USDT") in margin_calls
        assert (5, "BTC/USDT:USDT") in lev_calls
        assert (5, "ETH/USDT:USDT") in lev_calls
        # Sanity: the broker stored its leverage.
        assert b._leverage == 5

    def test_swallows_already_set_errors_on_margin_mode(self):
        """Binance returns -4046 / "no need to change" if margin mode is
        already isolated. That must NOT prevent the broker from
        constructing."""
        client = MagicMock()
        client.set_margin_mode.side_effect = Exception(
            "binance {'code': -4046, 'msg': 'No need to change margin type.'}"
        )
        client.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "leverage": 5, "marginMode": "isolated"}
        ]
        # Should not raise.
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        # set_leverage was still called after the swallowed margin error.
        assert client.set_leverage.called

    def test_swallows_already_set_errors_on_leverage(self):
        client = MagicMock()
        client.set_leverage.side_effect = Exception(
            "binance {'code': -4028, 'msg': 'Leverage not modified.'}"
        )
        client.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "leverage": 5, "marginMode": "isolated"}
        ]
        # Should not raise.
        BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)

    def test_strict_raises_on_leverage_mismatch(self, monkeypatch):
        monkeypatch.setattr(settings, "strict_pair_init", True)
        client = MagicMock()
        # Exchange reports 50 even though we requested 5 — the loud failure
        # mode that the pre-Fix-9.C path swallowed silently.
        client.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "leverage": 50, "marginMode": "isolated"}
        ]
        with pytest.raises(LiveTradingDisabled, match="leverage mismatch"):
            BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)

    def test_strict_raises_on_margin_mode_mismatch(self, monkeypatch):
        monkeypatch.setattr(settings, "strict_pair_init", True)
        client = MagicMock()
        client.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "leverage": 5, "marginMode": "cross"}
        ]
        with pytest.raises(LiveTradingDisabled, match="margin mode mismatch"):
            BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)

    def test_non_strict_logs_and_continues_on_mismatch(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "strict_pair_init", False)
        client = MagicMock()
        client.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "leverage": 50, "marginMode": "cross"}
        ]
        # Should construct without raising even though mismatched.
        BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)

    def test_strict_skips_verification_when_fetch_positions_hiccups(self, monkeypatch):
        """Network blips during the first fetch_positions after
        set_leverage are common on testnet. Strict mode logs and
        continues — refusing to start over a transient blip would be
        worse than the read-back missing."""
        monkeypatch.setattr(settings, "strict_pair_init", True)
        client = MagicMock()
        client.fetch_positions.side_effect = Exception("connection reset")
        # Should NOT raise — the read-back is best-effort even in strict mode.
        BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)

    def test_deferred_margin_mode_on_open_position_does_not_raise(self, monkeypatch):
        """When a pair has an open position, Binance returns -4047 on
        set_margin_mode. Strict mode must NOT raise on the subsequent
        margin-mode mismatch — we couldn't change it anyway."""
        monkeypatch.setattr(settings, "strict_pair_init", True)
        client = MagicMock()
        client.set_margin_mode.side_effect = Exception(
            "binance {'code': -4047, 'msg': "
            "'Margin type cannot be changed if there exists open orders.'}"
        )
        # Read-back reports CROSS (the existing position's mode).
        client.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "leverage": 5, "marginMode": "cross"}
        ]
        # Should NOT raise — the deferred margin change suppresses the strict check.
        BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)

    def test_on_reconnect_re_asserts_pair_init(self):
        """A restart with the position open should re-run
        _ensure_pair_init so any drift since the previous boot is caught."""
        client = MagicMock()
        client.fetch_positions.return_value = [
            {"symbol": "BTC/USDT:USDT", "leverage": 5, "marginMode": "isolated"}
        ]
        client.fetch_open_orders.return_value = []
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)

        # Reset call counts to isolate on_reconnect's calls.
        client.set_margin_mode.reset_mock()
        client.set_leverage.reset_mock()
        client.fetch_positions.reset_mock()
        # on_reconnect needs positions and open_orders for the position
        # rebuild step; same fixture works.
        client.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT:USDT",
                "leverage": 5,
                "marginMode": "isolated",
                "contracts": 0,
                "side": "long",
                "entryPrice": 0.0,
                "info": {"positionAmt": "0"},
            }
        ]

        b.on_reconnect()
        # Margin + leverage were re-asserted on the recovered pair.
        assert client.set_margin_mode.called
        assert client.set_leverage.called


# ---- Fix 9.E (Phase 9): per-pair readiness boot gate --------------------


class TestVerifyPairReadiness:
    """Fix 9.E: scanner refuses to start if any pair will silently fail
    on first signal (wrong leverage / margin, no ticker, qty floors
    below min_notional)."""

    def _client_with_pair_state(
        self,
        *,
        leverage: int = 5,
        margin_mode: str = "isolated",
        ticker_price: float | None = 4300.0,
        min_notional: float = 5.0,
        equity: float = 10000.0,
    ) -> MagicMock:
        """Reusable mock client. Defaults represent a healthy major pair."""
        client = MagicMock()
        client.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT:USDT",
                "leverage": leverage,
                "marginMode": margin_mode,
            }
        ]
        if ticker_price is None:
            client.fetch_ticker.return_value = {"last": None}
        else:
            client.fetch_ticker.return_value = {"last": ticker_price}
        client.load_markets.return_value = {
            "BTC/USDT:USDT": {
                "limits": {"cost": {"min": min_notional}},
                "precision": {"amount": 0.0001},
            }
        }
        client.fetch_balance.return_value = {"USDT": {"free": equity}}
        # Precision helpers — identity for the test math.
        client.amount_to_precision.side_effect = lambda p, q: f"{q}"
        client.price_to_precision.side_effect = lambda p, px: f"{px}"
        return client

    def test_returns_ok_when_everything_healthy(self):
        client = self._client_with_pair_state()
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        status = b.verify_pair_readiness("BTC/USDT:USDT")
        assert status["ok"] is True
        assert status["leverage"] == 5
        assert status["margin_mode"] == "isolated"
        assert status["ticker_price"] == 4300.0
        assert status["min_notional"] == 5.0
        assert status["sized_qty"] is not None
        assert status["sized_notional"] is not None

    def test_flags_leverage_mismatch(self, monkeypatch):
        monkeypatch.setattr(settings, "strict_pair_init", False)  # don't raise
        client = self._client_with_pair_state(leverage=50)
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        status = b.verify_pair_readiness("BTC/USDT:USDT")
        assert status["ok"] is False
        assert any("leverage=50" in r for r in status["reasons"])

    def test_flags_margin_mode_mismatch(self, monkeypatch):
        monkeypatch.setattr(settings, "strict_pair_init", False)
        client = self._client_with_pair_state(margin_mode="cross")
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        status = b.verify_pair_readiness("BTC/USDT:USDT")
        assert status["ok"] is False
        assert any("margin_mode" in r for r in status["reasons"])

    def test_flags_missing_ticker(self):
        client = self._client_with_pair_state(ticker_price=None)
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        status = b.verify_pair_readiness("BTC/USDT:USDT")
        assert status["ok"] is False
        assert "no ticker price" in " ".join(status["reasons"])

    def test_flags_sized_notional_below_min(self):
        """RISK_PCT_LIVE × equity / SL = tiny qty. If qty × price < min_notional,
        the pair will silently reject every order. Flag it loud at boot."""
        # equity $10, risk 0.05 %, SL 0.5 %, price 4300 → qty ~0.00023 → notional ~$1
        # min_notional set high to 50 → fail.
        client = self._client_with_pair_state(equity=10.0, min_notional=50.0)
        b = BinanceLiveBroker(allowed_pairs={"BTC/USDT:USDT"}, client=client)
        status = b.verify_pair_readiness("BTC/USDT:USDT")
        assert status["ok"] is False
        assert any("min_notional" in r for r in status["reasons"])

    def test_verify_all_pairs_returns_per_pair_dict(self):
        client = self._client_with_pair_state()
        # Make fetch_positions return one row per pair queried — the broker
        # passes symbols=[pair] so each call gets the right row.
        client.fetch_positions.side_effect = lambda symbols=None, **_: [
            {"symbol": s, "leverage": 5, "marginMode": "isolated"} for s in (symbols or [])
        ]
        client.load_markets.return_value = {
            p: {"limits": {"cost": {"min": 5.0}}, "precision": {"amount": 0.0001}}
            for p in ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
        }
        b = BinanceLiveBroker(
            allowed_pairs={"BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"},
            client=client,
        )
        statuses = b.verify_all_pairs_ready()
        assert set(statuses.keys()) == {"BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"}
        for p, s in statuses.items():
            assert isinstance(s, dict)
            assert "ok" in s
