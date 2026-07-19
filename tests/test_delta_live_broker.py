"""
DeltaLiveBroker tests. Every test mocks ccxt — nothing here hits Delta.

Covers the same broker contract as BinanceLiveBroker:
  - gating (ENABLE_LIVE_TRADING + allowed_pairs)
  - 3-leg bracket placement
  - SL/TP failure rollback (audit gap #5)
  - cancel() ripping down all three legs
  - reconcile_from_exchange marking FILLED on zero contracts

Plus Delta-specific:
  - coin → contract conversion (BTC = 0.001 BTC/contract)
  - integer-contracts quantization (qty step = 1.0)
  - sub-contract qty raises ValueError, doesn't silently drop
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from ictbot.exec.delta_live import DeltaLiveBroker, LiveTradingDisabled
from ictbot.exec.orders import Order
from ictbot.settings import settings


@pytest.fixture
def enable_live(monkeypatch):
    monkeypatch.setattr(settings, "enable_live_trading", True)
    yield


def _btc_order(coin_qty: float = 0.05) -> Order:
    """BTC perpetual order — contract size 0.001, so 0.05 BTC = 50 contracts."""
    return Order(
        pair="BTC/USDT:USDT",
        side="BUY",
        entry=100_000.0,
        sl=99_000.0,
        tp=103_000.0,
        qty=coin_qty,
    )


def _broker_with_lookups(client, contract_size=0.001, qty_step=1.0) -> DeltaLiveBroker:
    return DeltaLiveBroker(
        allowed_pairs={"BTC/USDT:USDT"},
        client=client,
        contract_size_lookup=lambda p: contract_size,
        qty_step_lookup=lambda p: qty_step,
    )


# ---- gating ----------------------------------------------------------------


def test_place_order_refuses_when_live_disabled(monkeypatch):
    # Force the gate OFF explicitly — don't rely on the ambient .env (which may be live-armed
    # for the BNB/TWAK go-live; ENABLE_LIVE_TRADING is a shared cross-broker flag).
    monkeypatch.setattr(settings, "enable_live_trading", False)
    client = MagicMock()
    b = _broker_with_lookups(client)
    with pytest.raises(LiveTradingDisabled, match="ENABLE_LIVE_TRADING"):
        b.place_order(_btc_order())
    assert client.create_order.call_count == 0


def test_place_order_refuses_unknown_pair(enable_live):
    client = MagicMock()
    b = DeltaLiveBroker(
        allowed_pairs={"BTC/USDT:USDT"},
        client=client,
        contract_size_lookup=lambda p: 0.001,
        qty_step_lookup=lambda p: 1.0,
    )
    order = _btc_order()
    order.pair = "DOGE/USDT:USDT"
    with pytest.raises(LiveTradingDisabled, match="allowed_pairs"):
        b.place_order(order)


# ---- coin → contract conversion -------------------------------------------


def test_place_order_converts_coin_qty_to_contracts(enable_live):
    """0.05 BTC ÷ 0.001 BTC/contract = 50 contracts."""
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry"},
        {"id": "sl"},
        {"id": "tp"},
    ]
    b = _broker_with_lookups(client)
    o = b.place_order(_btc_order(coin_qty=0.05))

    assert o.qty == 50  # mutated in place
    # Entry was placed with 50 contracts, not 0.05.
    entry_call = client.create_order.call_args_list[0]
    assert entry_call == call("BTC/USDT:USDT", "market", "buy", 50)


def test_place_order_floors_to_qty_step(enable_live):
    """0.0153 BTC ÷ 0.001 = 15.3 contracts → floor to 15 (step = 1.0)."""
    client = MagicMock()
    client.create_order.side_effect = [{"id": "entry"}, {"id": "sl"}, {"id": "tp"}]
    b = _broker_with_lookups(client)
    o = b.place_order(_btc_order(coin_qty=0.0153))
    assert o.qty == 15


def test_place_order_rejects_sub_contract_qty(enable_live):
    """0.0001 BTC ÷ 0.001 = 0.1 contracts → floor 0 → ValueError, not silent."""
    client = MagicMock()
    b = _broker_with_lookups(client)
    with pytest.raises(ValueError, match="too small to trade"):
        b.place_order(_btc_order(coin_qty=0.0001))
    # No ccxt call was made.
    assert client.create_order.call_count == 0


# ---- 3-leg bracket placement ----------------------------------------------


def test_place_order_issues_three_ccxt_calls(enable_live):
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "e1"},
        {"id": "s1"},
        {"id": "t1"},
    ]
    b = _broker_with_lookups(client)
    o = b.place_order(_btc_order())

    assert client.create_order.call_count == 3
    assert o.entry_order_id == "e1"
    assert o.sl_order_id == "s1"
    assert o.tp_order_id == "t1"
    assert o.status == "OPEN"


def test_sl_uses_reduce_only_stop_market(enable_live):
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
    b = _broker_with_lookups(client)
    b.place_order(_btc_order())

    sl_call = client.create_order.call_args_list[1]
    # (pair, "stop_market", opposite, qty, None, {"stopPrice": sl, "reduceOnly": True})
    pair, otype, side, qty, price, params = sl_call.args
    assert otype == "stop_market"
    assert side == "sell"  # opposite of BUY
    assert params == {"stopPrice": 99_000.0, "reduceOnly": True}


def test_tp_uses_reduce_only_limit(enable_live):
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
    b = _broker_with_lookups(client)
    b.place_order(_btc_order())

    tp_call = client.create_order.call_args_list[2]
    pair, otype, side, qty, price, params = tp_call.args
    assert otype == "limit"
    assert side == "sell"
    assert price == 103_000.0
    assert params == {"reduceOnly": True}


# ---- rollback safety (audit gap #5) ---------------------------------------


def test_sl_failure_triggers_emergency_flatten(enable_live):
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry"},
        RuntimeError("rate limited"),  # SL fails
        {"id": "flatten"},  # emergency flatten
    ]
    b = _broker_with_lookups(client)

    with pytest.raises(RuntimeError, match="rate limited"):
        b.place_order(_btc_order())

    assert client.create_order.call_count == 3
    flatten_call = client.create_order.call_args_list[2]
    assert flatten_call == call("BTC/USDT:USDT", "market", "sell", 50, None, {"reduceOnly": True})
    assert b._orders == {}  # no leaked open order


def test_tp_failure_cancels_sl_and_flattens(enable_live):
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry"},
        {"id": "sl"},
        RuntimeError("invalid limit"),
        {"id": "flatten"},
    ]
    b = _broker_with_lookups(client)

    with pytest.raises(RuntimeError, match="invalid limit"):
        b.place_order(_btc_order())

    client.cancel_order.assert_called_once_with("sl", "BTC/USDT:USDT")
    flatten_call = client.create_order.call_args_list[-1]
    assert flatten_call == call("BTC/USDT:USDT", "market", "sell", 50, None, {"reduceOnly": True})
    assert b._orders == {}


def test_flatten_failure_logs_critical_but_reraises_original(enable_live, caplog):
    import logging

    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry"},
        RuntimeError("SL failed"),
        RuntimeError("FLATTEN ALSO FAILED"),
    ]
    b = _broker_with_lookups(client)

    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(RuntimeError, match="SL failed"):
            b.place_order(_btc_order())
    assert any("EMERGENCY FLATTEN FAILED" in rec.message for rec in caplog.records)


# ---- cancel ----------------------------------------------------------------


def test_cancel_rips_down_all_three_legs(enable_live):
    client = MagicMock()
    client.create_order.side_effect = [
        {"id": "entry"},
        {"id": "sl"},
        {"id": "tp"},
        {"id": "flatten"},
    ]
    b = _broker_with_lookups(client)
    o = b.place_order(_btc_order())

    ok = b.cancel(o.id)
    assert ok is True
    # Two cancel_order calls (SL + TP) plus one reduce-only market flatten.
    assert client.cancel_order.call_count == 2
    flatten = client.create_order.call_args_list[-1]
    assert flatten == call("BTC/USDT:USDT", "market", "sell", 50, None, {"reduceOnly": True})
    assert o.status == "CANCELLED"


# ---- reconcile -------------------------------------------------------------


def test_reconcile_intermittent_zero_does_not_close_order(enable_live):
    """If fetch_positions reports zero, then live, then zero again — the
    streak must reset on the live read so the order doesn't accidentally
    finalize on the third call."""
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
    # zero → live → zero
    client.fetch_positions.side_effect = [
        [],
        [{"symbol": "BTC/USDT:USDT", "contracts": 50}],
        [],
    ]
    b = _broker_with_lookups(client)
    o = b.place_order(_btc_order())

    b.positions()  # zero, streak=1
    b.positions()  # live, streak resets
    b.positions()  # zero again, streak=1 (NOT 3)
    assert o.status == "OPEN"


def test_reconcile_marks_filled_after_two_consecutive_zero_reads(enable_live):
    """§K (audit gap #15): a single transient empty read doesn't free
    the cap. Two consecutive empty reads on the same pair do."""
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
    client.fetch_positions.return_value = []
    client.fetch_order.return_value = {"id": "s", "status": "closed", "average": 98_950.0}
    b = _broker_with_lookups(client)
    o = b.place_order(_btc_order())

    # First zero read: streak = 1, order still OPEN.
    open_after_first = b.positions()
    assert len(open_after_first) == 1
    assert o.status == "OPEN"

    # Second zero read: streak hits 2 → FILLED with the resolved SL fill.
    open_after_second = b.positions()
    assert open_after_second == []
    assert o.status == "FILLED"
    assert o.close_reason == "SL"


def test_reconcile_keeps_order_open_when_exchange_still_holds_it(enable_live):
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "s"}, {"id": "t"}]
    client.fetch_positions.return_value = [{"symbol": "BTC/USDT:USDT", "contracts": 50}]
    b = _broker_with_lookups(client)
    o = b.place_order(_btc_order())

    positions = b.positions()
    assert len(positions) == 1
    assert positions[0] is o
    assert o.status == "OPEN"


def test_on_reconnect_creates_stubs_for_pre_existing_positions(enable_live):
    client = MagicMock()
    client.fetch_positions.return_value = [
        {
            "symbol": "BTC/USDT:USDT",
            "contracts": 25,
            "side": "long",
            "entryPrice": 100_000,
        }
    ]
    b = _broker_with_lookups(client)
    b.on_reconnect()

    stubs = [o for o in b._orders.values()]
    assert len(stubs) == 1
    assert stubs[0].pair == "BTC/USDT:USDT"
    assert stubs[0].side == "BUY"
    assert stubs[0].qty == 25
    assert stubs[0].status == "OPEN"


# ---- set_leverage safety ---------------------------------------------------


def test_construction_sets_leverage_on_every_allowed_pair():
    """Audit gap #I: live brokers must override the venue's default
    leverage (often 50× on Delta's UI). Test that set_leverage is called
    with our configured value for every pair we said we'd trade."""
    client = MagicMock()
    DeltaLiveBroker(
        allowed_pairs={"BTC/USDT:USDT", "ETH/USDT:USDT"},
        client=client,
        contract_size_lookup=lambda p: 0.001,
        qty_step_lookup=lambda p: 1.0,
        leverage=5,
    )
    # Both pairs called with leverage=5. Order is set-iteration, so check
    # the call set rather than the sequence.
    calls = [c.args for c in client.set_leverage.call_args_list]
    assert (5, "BTC/USDT:USDT") in calls
    assert (5, "ETH/USDT:USDT") in calls
    assert client.set_leverage.call_count == 2


def test_set_leverage_failure_does_not_block_construction():
    """Testnet / missing pairs / API hiccups must not stop the broker
    from coming up — the kill switch + allowed_pairs are still in effect."""
    client = MagicMock()
    client.set_leverage.side_effect = RuntimeError("pair not listed on testnet")
    b = DeltaLiveBroker(
        allowed_pairs={"BTC/USDT:USDT"},
        client=client,
        contract_size_lookup=lambda p: 0.001,
        qty_step_lookup=lambda p: 1.0,
    )
    assert b.allowed_pairs == {"BTC/USDT:USDT"}


# ---- _on_close wiring (close events feed caps) -----------------------------


def test_reconcile_filled_with_sl_hit_fires_on_close(enable_live):
    """When fetch_positions shows empty but fetch_order(sl_id) returns
    status=closed, the broker should mark close_reason=SL, set
    close_price to the SL fill price, and fire _on_close."""
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "sl"}, {"id": "tp"}]
    client.fetch_positions.return_value = []  # position gone
    client.fetch_order.side_effect = [
        {"id": "sl", "status": "closed", "average": 98_950.0},  # SL leg filled
    ]
    closed_events: list = []
    b = DeltaLiveBroker(
        allowed_pairs={"BTC/USDT:USDT"},
        client=client,
        contract_size_lookup=lambda p: 0.001,
        qty_step_lookup=lambda p: 1.0,
        on_close=closed_events.append,
    )
    o = b.place_order(_btc_order())
    b.positions()  # first zero read — streak=1, still OPEN
    b.positions()  # second zero read — finalizes

    assert o.status == "FILLED"
    assert o.close_reason == "SL"
    assert o.close_price == 98_950.0
    assert len(closed_events) == 1
    assert closed_events[0] is o
    # realised_pnl_R is negative (SL hit on a BUY).
    assert o.realised_pnl_R() < 0


def test_reconcile_filled_with_tp_hit_fires_on_close(enable_live):
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "sl"}, {"id": "tp"}]
    client.fetch_positions.return_value = []
    # SL leg shows still open; TP leg shows closed.
    client.fetch_order.side_effect = [
        {"id": "sl", "status": "open"},
        {"id": "tp", "status": "closed", "average": 103_010.0},
    ]
    closed_events: list = []
    b = DeltaLiveBroker(
        allowed_pairs={"BTC/USDT:USDT"},
        client=client,
        contract_size_lookup=lambda p: 0.001,
        qty_step_lookup=lambda p: 1.0,
        on_close=closed_events.append,
    )
    o = b.place_order(_btc_order())
    b.positions()
    b.positions()  # second zero read finalizes

    assert o.close_reason == "TP"
    assert o.close_price == 103_010.0
    assert len(closed_events) == 1
    # realised R > 0 for a TP hit.
    assert o.realised_pnl_R() > 0


def test_reconcile_filled_with_unresolvable_legs_marks_manual(enable_live):
    """If both fetch_order lookups fail, we still mark FILLED + fire
    _on_close, but close_price = entry → realised R = 0. Caps stay live;
    we don't fabricate a result."""
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "sl"}, {"id": "tp"}]
    client.fetch_positions.return_value = []
    client.fetch_order.side_effect = RuntimeError("network blip")
    closed_events: list = []
    b = DeltaLiveBroker(
        allowed_pairs={"BTC/USDT:USDT"},
        client=client,
        contract_size_lookup=lambda p: 0.001,
        qty_step_lookup=lambda p: 1.0,
        on_close=closed_events.append,
    )
    o = b.place_order(_btc_order())
    b.positions()
    b.positions()  # second zero read finalizes

    assert o.status == "FILLED"
    assert o.close_reason == "MANUAL"
    assert o.close_price == o.entry
    assert o.realised_pnl_R() == 0.0
    assert len(closed_events) == 1


def test_on_close_callback_exception_does_not_break_broker(enable_live):
    """A buggy callback on the receiver side must not corrupt broker
    state — the order is FILLED regardless."""
    client = MagicMock()
    client.create_order.side_effect = [{"id": "e"}, {"id": "sl"}, {"id": "tp"}]
    client.fetch_positions.return_value = []
    client.fetch_order.return_value = {"id": "sl", "status": "closed", "average": 98_900.0}

    def bad_callback(o):
        raise RuntimeError("downstream broke")

    b = DeltaLiveBroker(
        allowed_pairs={"BTC/USDT:USDT"},
        client=client,
        contract_size_lookup=lambda p: 0.001,
        qty_step_lookup=lambda p: 1.0,
        on_close=bad_callback,
    )
    o = b.place_order(_btc_order())
    b.positions()
    b.positions()  # second zero read finalizes — must not raise

    assert o.status == "FILLED"
    assert o.close_reason == "SL"
