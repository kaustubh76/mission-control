"""
Tests for core.journal. Uses tmp_path + monkeypatch to keep the real
signals.json untouched.
"""

import json

from ictbot import settings as config
from ictbot.portfolio import journal


def _patch_journal(tmp_path, monkeypatch):
    fake = tmp_path / "signals.json"
    monkeypatch.setattr(config, "JOURNAL_FILE", fake)
    monkeypatch.setattr(journal, "JOURNAL_FILE", fake)
    return fake


def test_read_empty_when_missing(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    assert journal.read_journal() == []


def test_append_then_read(tmp_path, monkeypatch):
    fake = _patch_journal(tmp_path, monkeypatch)
    journal.append_signal(
        "BTC/USDT:USDT", "BUY", price=100.0, sl=99.0, tp=103.0, rr=3.0, confidence=100
    )
    entries = journal.read_journal()
    assert len(entries) == 1
    e = entries[0]
    assert e["pair"] == "BTC/USDT:USDT"
    assert e["entry"] == "BUY"
    assert e["outcome"] == "OPEN"
    assert e["closed_ts"] is None
    # File on disk is valid JSON
    json.loads(fake.read_text())


def test_filter_by_pair(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "BUY", 100, 99, 103, 3, 100)
    journal.append_signal("ETH/USDT:USDT", "SELL", 50, 51, 47, 3, 100)
    assert len(journal.read_journal(pair="BTC/USDT:USDT")) == 1
    assert journal.read_journal(pair="ETH/USDT:USDT")[0]["entry"] == "SELL"


# ---- Fix 16.A (Phase 16) session field round-trip --------------------------


def test_append_signal_persists_session(tmp_path, monkeypatch):
    """Phase 16: append_signal accepts an optional session label and the
    field round-trips through read_journal. Default None preserves
    backwards-compat for callers that don't pass it (e.g. tests)."""
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal(
        "BTC/USDT:USDT",
        "BUY",
        100,
        99,
        103,
        3,
        100,
        session="LONDON",
    )
    journal.append_signal(
        "ETH/USDT:USDT",
        "SELL",
        50,
        51,
        47,
        3,
        100,
        # No session kwarg → field defaults to None.
    )
    entries = journal.read_journal()
    assert entries[0]["session"] == "LONDON"
    assert entries[1]["session"] is None


def test_settle_buy_win(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "BUY", 100, 99, 103, 3, 100)
    # Next bar high = 104 → TP hit → WIN
    n = journal.settle_open_signals({"BTC/USDT:USDT": {"high": 104, "low": 101}})
    assert n == 1
    assert journal.read_journal()[0]["outcome"] == "WIN"


def test_settle_buy_loss(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "BUY", 100, 99, 103, 3, 100)
    # Next bar low = 98 → SL hit → LOSS
    n = journal.settle_open_signals({"BTC/USDT:USDT": {"high": 100.5, "low": 98}})
    assert n == 1
    assert journal.read_journal()[0]["outcome"] == "LOSS"


def test_settle_sell_win(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "SELL", 100, 101, 97, 3, 100)
    # Next bar low = 96 → TP hit → WIN
    n = journal.settle_open_signals({"BTC/USDT:USDT": {"high": 100, "low": 96}})
    assert n == 1
    assert journal.read_journal()[0]["outcome"] == "WIN"


def test_settle_sell_loss(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "SELL", 100, 101, 97, 3, 100)
    # Next bar high = 102 → SL hit → LOSS
    n = journal.settle_open_signals({"BTC/USDT:USDT": {"high": 102, "low": 99}})
    assert n == 1
    assert journal.read_journal()[0]["outcome"] == "LOSS"


def test_settle_skips_already_closed(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "BUY", 100, 99, 103, 3, 100)
    journal.settle_open_signals({"BTC/USDT:USDT": {"high": 104, "low": 101}})
    # Now settle again with a SL hit — already WIN, must stay WIN
    journal.settle_open_signals({"BTC/USDT:USDT": {"high": 100, "low": 98}})
    assert journal.read_journal()[0]["outcome"] == "WIN"


def test_settle_skips_rejected_rows(tmp_path, monkeypatch):
    """REJECTED rows (cap-rejected signals written by _journal_rejected) are
    not real positions and must NEVER be settled — the bar high/low has no
    meaning for an order that was never placed. Regression for the
    2026-06-05 phantom-close incident."""
    fake = _patch_journal(tmp_path, monkeypatch)
    # Use the underlying _write to inject a row with the same shape the
    # router emits on cap rejection.
    import datetime as _dt

    entries = [
        {
            "ts": _dt.datetime(2026, 6, 5, 11, 11, tzinfo=_dt.timezone.utc).isoformat(),
            "pair": "SOL/USDT:USDT",
            "entry": "REJECTED (max_open_positions (1) reached (1 currently open))",
            "price": 65.94,
            "sl": 66.27,
            "tp": 64.99,
            "rr": 3.0,
            "confidence": 100,
            "outcome": "OPEN",
            "closed_ts": None,
            "closed_price": None,
        }
    ]
    journal._write(fake, entries)
    # Simulate a bar that would hit the REJECTED row's "sl" if it were real.
    n = journal.settle_open_signals({"SOL/USDT:USDT": {"high": 67.0, "low": 65.5}})
    assert n == 0  # nothing real to settle
    assert journal.read_journal()[0]["outcome"] == "OPEN"  # row unchanged


def test_settle_skips_live_broker_rows(tmp_path, monkeypatch):
    """Fix 2.B (plan: live P&L clean-up): synthetic settler must skip
    rows tagged with a non-paper broker. The broker's _on_close path is
    the only correct close source for real exchange fills; if the
    settler closes the row from bar OHLC, the recorded closed_price
    will be bit-for-bit equal to sl/tp, masking the real fill price
    (regression for the 2026-06-05 binance-testnet diagnostic: 46/46
    WIN rows had closed_price == tp exactly).
    """
    _patch_journal(tmp_path, monkeypatch)
    # Paper row — should settle normally.
    journal.append_signal("BTC/USDT:USDT", "BUY", 100, 99, 103, 3, 100, broker="paper")
    # Live row — must NOT settle even when bar high crosses tp.
    journal.append_signal("ETH/USDT:USDT", "BUY", 50, 49, 53, 3, 100, broker="binance-live")
    n = journal.settle_open_signals(
        {
            "BTC/USDT:USDT": {"high": 104, "low": 101},  # would WIN
            "ETH/USDT:USDT": {"high": 54, "low": 51},  # would WIN too
        }
    )
    assert n == 1  # only the paper row was settled
    rows = journal.read_journal()
    paper_row = next(r for r in rows if r["pair"] == "BTC/USDT:USDT")
    live_row = next(r for r in rows if r["pair"] == "ETH/USDT:USDT")
    assert paper_row["outcome"] == "WIN"
    assert live_row["outcome"] == "OPEN"
    assert live_row["closed_price"] is None  # broker truth still pending


def test_settle_treats_no_broker_field_as_paper(tmp_path, monkeypatch):
    """Backwards-compat: pre-Fix-2.A journal rows have no broker field.
    The settler must treat them as paper so historical paper rows keep
    settling normally during a rolling upgrade."""
    fake = _patch_journal(tmp_path, monkeypatch)
    import datetime as _dt

    entries = [
        {
            "ts": _dt.datetime(2026, 6, 4, 8, 0, tzinfo=_dt.timezone.utc).isoformat(),
            "pair": "BTC/USDT:USDT",
            "entry": "BUY",
            "price": 100.0,
            "sl": 99.0,
            "tp": 103.0,
            "rr": 3.0,
            "confidence": 100,
            "outcome": "OPEN",
            "closed_ts": None,
            "closed_price": None,
            # NOTE: no "broker" key — pre-fix shape
        }
    ]
    journal._write(fake, entries)
    n = journal.settle_open_signals({"BTC/USDT:USDT": {"high": 104, "low": 101}})
    assert n == 1
    assert journal.read_journal()[0]["outcome"] == "WIN"


def test_mark_closed_writes_pnl_r_and_fee_fields(tmp_path, monkeypatch):
    """Fix 2.F: mark_closed_from_broker must persist pnl_r,
    entry_fill_price, and fees_paid into the close row so post-hoc
    analysis can derive net (fee-inclusive) realised R without re-
    deriving from prices."""
    from ictbot.exec.orders import Order

    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal(
        "BTC/USDT:USDT", "BUY", 100.0, 95.0, 110.0, 3.0, 100, broker="binance-live"
    )
    o = Order(pair="BTC/USDT:USDT", side="BUY", entry=100.05, sl=95.0, tp=110.0, qty=0.5)
    o.status = "FILLED"
    o.close_price = 110.0
    o.close_reason = "TP"
    o.fees_paid = 0.5
    journal.mark_closed_from_broker(o)
    row = journal.read_journal()[0]
    assert row["outcome"] == "WIN"
    assert row["entry_fill_price"] == 100.05  # the broker's actual fill
    assert row["fees_paid"] == 0.5
    # Gross R = (110 - 100.05) / (100.05 - 95) = 1.9703 ish.
    # fees_R = 0.5 / (0.5 × 5.05) = 0.198. Net ≈ 1.772.
    assert row["pnl_r"] is not None
    assert row["pnl_r"] < 2.0  # net R is below the legacy gross
    assert row["pnl_r"] > 1.7


def test_append_signal_default_broker_is_paper(tmp_path, monkeypatch):
    """append_signal called positionally (legacy call shape) must still
    populate broker="paper" so the gate in settle_open_signals works."""
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "BUY", 100, 99, 103, 3, 100)
    row = journal.read_journal()[0]
    assert row["broker"] == "paper"


def test_score_journal(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "BUY", 100, 99, 103, 3, 100)
    journal.append_signal("ETH/USDT:USDT", "SELL", 50, 51, 47, 3, 100)
    journal.append_signal("SOL/USDT:USDT", "BUY", 200, 199, 203, 3, 100)
    journal.settle_open_signals({"BTC/USDT:USDT": {"high": 104, "low": 101}})  # WIN
    journal.settle_open_signals({"ETH/USDT:USDT": {"high": 52, "low": 49}})  # LOSS
    s = journal.score_journal()
    assert s["total"] == 3
    assert s["wins"] == 1
    assert s["losses"] == 1
    assert s["open"] == 1
    assert s["win_rate"] == 50.0
