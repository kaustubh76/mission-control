"""
Fix 2.H tests for `shadow_report --by-broker`.

Uses tmp_path + monkeypatch to seed a synthetic journal with both
broker tags and asserts the per-pair R split renders cleanly.
"""

from __future__ import annotations

from ictbot import settings as config
from ictbot.cli import shadow_report
from ictbot.portfolio import journal


def _patch_journal(tmp_path, monkeypatch):
    fake = tmp_path / "signals.json"
    monkeypatch.setattr(config, "JOURNAL_FILE", fake)
    monkeypatch.setattr(journal, "JOURNAL_FILE", fake)
    return fake


def _close_paper_win(pair="BTC/USDT:USDT"):
    """Append + settle a winning paper BUY so the row has WIN outcome."""
    journal.append_signal(pair, "BUY", 100, 99, 103, 3.0, 100, broker="paper")
    journal.settle_open_signals({pair: {"high": 104, "low": 101}})


def _close_paper_loss(pair="BTC/USDT:USDT"):
    journal.append_signal(pair, "BUY", 100, 99, 103, 3.0, 100, broker="paper")
    journal.settle_open_signals({pair: {"high": 100.5, "low": 98}})


def _write_live_row(tmp_path, *, pair, outcome, pnl_r):
    """Inject a live broker row with the post-Fix-2.F shape (pnl_r is
    set by mark_closed_from_broker)."""
    fake = config.JOURNAL_FILE
    import datetime as _dt

    existing = journal._read(fake)
    existing.append(
        {
            "ts": _dt.datetime(2026, 6, 5, 12, 0, tzinfo=_dt.timezone.utc).isoformat(),
            "pair": pair,
            "entry": "BUY",
            "price": 100.0,
            "sl": 95.0,
            "tp": 110.0,
            "rr": 3.0,
            "confidence": 100,
            "outcome": outcome,
            "closed_ts": _dt.datetime(2026, 6, 5, 12, 5, tzinfo=_dt.timezone.utc).isoformat(),
            "closed_price": 110.0 if outcome == "WIN" else 95.0,
            "broker": "binance-live",
            "pnl_r": pnl_r,
            "entry_fill_price": 100.05,
            "fees_paid": 0.5,
        }
    )
    journal._write(fake, existing)


def test_by_broker_splits_live_and_paper_rows(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    _close_paper_win()  # paper row, WIN
    _close_paper_loss()  # paper row, LOSS
    _write_live_row(tmp_path, pair="BTC/USDT:USDT", outcome="WIN", pnl_r=2.7)
    _write_live_row(tmp_path, pair="BTC/USDT:USDT", outcome="LOSS", pnl_r=-1.2)

    out = shadow_report._by_broker_summary()
    # Headers + both broker columns present
    assert "binance-live" in out
    assert "paper" in out
    assert "BTC/USDT:USDT" in out
    # Pairwise delta is rendered when both legs have data
    assert "binance-live − paper" in out


def test_by_broker_handles_empty_journal(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    assert shadow_report._by_broker_summary() == "(no journal entries yet)"


def test_by_broker_skips_open_and_rejected_rows(tmp_path, monkeypatch):
    """OPEN + REJECTED rows must NOT contribute to the per-broker
    score (only WIN/LOSS/BE/CLOSED do)."""
    _patch_journal(tmp_path, monkeypatch)
    journal.append_signal("BTC/USDT:USDT", "BUY", 100, 99, 103, 3.0, 100, broker="paper")
    journal.append_signal(
        "ETH/USDT:USDT", "REJECTED (cap)", 50, 49, 53, 3.0, 100, broker="binance-live"
    )
    out = shadow_report._by_broker_summary()
    # OPEN row has no closed outcome; rejected row has invalid entry side.
    # Both must be excluded → nothing closed → fallback message.
    assert out == "(no closed rows in journal yet)"


def test_build_report_includes_by_broker_when_flagged(tmp_path, monkeypatch):
    _patch_journal(tmp_path, monkeypatch)
    _close_paper_win()
    report_default = shadow_report.build_report(by_broker=False)
    report_with_split = shadow_report.build_report(by_broker=True)
    assert "JOURNAL — per-pair R by broker" not in report_default
    assert "JOURNAL — per-pair R by broker" in report_with_split
