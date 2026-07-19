"""
Multi-destination Telegram send.

Single-destination semantics had `TELEGRAM_CHAT_ID` as a scalar string.
We extended it to a comma-separated list so the bot can fan out to
e.g. the operator's DM + a public channel + a tester group from one
env var, without any code change downstream.

Invariants under test:
  - one destination → one POST, unchanged behaviour.
  - multi-destination → one POST per destination in order, all sent
    even when an earlier one fails.
  - send_telegram() returns True if ANY destination accepted, False
    only when EVERY destination failed (so the scanner's existing
    `sent=True/False` log line still means what it used to).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ictbot.notify import telegram as tg


@pytest.fixture
def _set_creds(monkeypatch):
    """Provide a fake bot token; tests set chat_id per-case."""
    monkeypatch.setattr(tg, "TELEGRAM_TOKEN", "TEST_TOKEN")
    yield


def _ok_response() -> MagicMock:
    m = MagicMock()
    m.raise_for_status.return_value = None
    return m


def test_no_destinations_skips_and_returns_false(_set_creds, monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_CHAT_ID", "")
    with patch("ictbot.notify.telegram.requests.post") as post:
        assert tg.send_telegram("hi") is False
    post.assert_not_called()


def test_single_destination_one_post(_set_creds, monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_CHAT_ID", "12345")
    with patch("ictbot.notify.telegram.requests.post") as post:
        post.return_value = _ok_response()
        assert tg.send_telegram("hello") is True
    assert post.call_count == 1
    _, kwargs = post.call_args
    assert kwargs["data"] == {"chat_id": "12345", "text": "hello"}


def test_multi_destination_fans_out_in_order(_set_creds, monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_CHAT_ID", "12345,@ict_signals_public,-1009999999999")
    with patch("ictbot.notify.telegram.requests.post") as post:
        post.return_value = _ok_response()
        assert tg.send_telegram("hello") is True
    assert post.call_count == 3
    sent_chat_ids = [c.kwargs["data"]["chat_id"] for c in post.call_args_list]
    assert sent_chat_ids == ["12345", "@ict_signals_public", "-1009999999999"]


def test_whitespace_and_empty_entries_are_trimmed(_set_creds, monkeypatch):
    """An env var written across multiple lines or with stray commas
    must not blow up or POST chat_id=''."""
    monkeypatch.setattr(tg, "TELEGRAM_CHAT_ID", "  12345 , ,@foo, ")
    with patch("ictbot.notify.telegram.requests.post") as post:
        post.return_value = _ok_response()
        assert tg.send_telegram("hi") is True
    sent_chat_ids = [c.kwargs["data"]["chat_id"] for c in post.call_args_list]
    assert sent_chat_ids == ["12345", "@foo"]


def test_one_failing_destination_does_not_block_others(_set_creds, monkeypatch):
    """The scanner's heartbeat keeps flowing to the channel even if the
    operator's DM is broken (bot blocked, chat archived, etc.)."""
    monkeypatch.setattr(tg, "TELEGRAM_CHAT_ID", "BAD,GOOD")
    bad = MagicMock()
    bad.raise_for_status.side_effect = RuntimeError("403 Forbidden")
    with patch("ictbot.notify.telegram.requests.post") as post:
        post.side_effect = [bad, _ok_response()]
        assert tg.send_telegram("hi") is True  # at least one accepted
    assert post.call_count == 2


def test_all_destinations_failing_returns_false(_set_creds, monkeypatch):
    """If every destination fails, the scanner should see sent=False so
    its `tg heartbeat sent=%s` line reports a real outage."""
    monkeypatch.setattr(tg, "TELEGRAM_CHAT_ID", "A,B")
    fail = MagicMock()
    fail.raise_for_status.side_effect = RuntimeError("503")
    with patch("ictbot.notify.telegram.requests.post") as post:
        post.return_value = fail
        assert tg.send_telegram("hi") is False
    assert post.call_count == 2
