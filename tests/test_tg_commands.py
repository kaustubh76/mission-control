"""
Phase D — TG operator commands tests.

All Telegram I/O is mocked. We exercise the command handlers directly
(they're async methods on TGConfirmService) and assert:
  - operator-only guard (non-operator drops silently)
  - /status / /journal / /kill / /resume / /pause / /whoami all reply
  - /kill engages kill_switch
  - /resume yes releases BOTH kill_switch and pause (and only on "yes")
  - /pause N engages pause for N minutes
  - ENABLE_LIVE_TRADING is never touched by /resume
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ictbot.notify.tg_confirm import TGConfirmService
from ictbot.runtime import kill_switch, pause

# ---- fixtures + helpers ---------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Each test gets fresh kill-switch + pause files."""
    monkeypatch.setattr(kill_switch, "KILL_SENTINEL", tmp_path / "KILL_SWITCH_ENGAGED")
    monkeypatch.setattr(kill_switch, "ENV_FILE", tmp_path / ".env-does-not-exist")
    monkeypatch.setattr(pause, "PAUSED_UNTIL_FILE", tmp_path / "PAUSED_UNTIL")
    yield


def _make_service(operator_user_id: int = 42) -> TGConfirmService:
    return TGConfirmService(
        token="test:fake",
        operator_user_id=operator_user_id,
        confirm_timeout_s=180,
        enable_commands=True,
    )


def _make_update(user_id: int, *, text: str = "") -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_ctx(args: list[str] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---- operator-only guard --------------------------------------------------


def test_non_operator_user_silently_dropped():
    """Any command from a non-operator user must produce no reply."""
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=999)  # NOT the operator
    ctx = _make_ctx(args=["10"])
    for handler in (
        svc._cmd_status,
        svc._cmd_journal,
        svc._cmd_kill,
        svc._cmd_resume,
        svc._cmd_pause,
        svc._cmd_help,
    ):
        update.message.reply_text.reset_mock()
        asyncio.run(handler(update, ctx))
        update.message.reply_text.assert_not_called()


# ---- /whoami --------------------------------------------------------------


def test_whoami_replies_with_ids_and_match_glyph():
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_whoami(update, _make_ctx()))
    update.message.reply_text.assert_called_once()
    body = update.message.reply_text.call_args.args[0]
    assert "operator_id = 42" in body
    assert "you        = 42" in body
    assert "✅" in body


def test_whoami_shows_mismatch_for_non_operator():
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=999)
    asyncio.run(svc._cmd_whoami(update, _make_ctx()))
    # No operator-only guard on /whoami (it's diagnostic) — reply fires
    update.message.reply_text.assert_called_once()
    body = update.message.reply_text.call_args.args[0]
    assert "operator_id = 42" in body
    assert "you        = 999" in body
    assert "❌" in body


# ---- /kill ----------------------------------------------------------------


def test_kill_engages_kill_switch_with_reason():
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_kill(update, _make_ctx(args=["debugging", "thing"])))
    assert kill_switch.is_engaged()
    update.message.reply_text.assert_called_once()
    body = update.message.reply_text.call_args.args[0]
    assert "KILL SWITCH ENGAGED" in body
    assert "debugging thing" in body


def test_kill_defaults_reason_when_none_given():
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_kill(update, _make_ctx(args=[])))
    assert kill_switch.is_engaged()


# ---- /resume --------------------------------------------------------------


def test_resume_yes_releases_kill_and_pause():
    kill_switch.engage(reason="pre-test")
    pause.engage(seconds=600)
    assert kill_switch.is_engaged()
    assert pause.is_active()

    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_resume(update, _make_ctx(args=["yes"])))

    assert not kill_switch.is_engaged()
    assert not pause.is_active()
    body = update.message.reply_text.call_args.args[0]
    assert "cleared" in body.lower()
    # Strict policy: ENABLE_LIVE_TRADING stays a manual step.
    assert "ENABLE_LIVE_TRADING" in body


def test_resume_without_yes_is_noop_with_hint():
    kill_switch.engage(reason="pre-test")
    pause.engage(seconds=600)

    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_resume(update, _make_ctx(args=[])))

    # Nothing released.
    assert kill_switch.is_engaged()
    assert pause.is_active()
    body = update.message.reply_text.call_args.args[0]
    assert "Usage" in body


def test_resume_with_wrong_arg_is_noop():
    kill_switch.engage(reason="pre-test")
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_resume(update, _make_ctx(args=["maybe"])))
    assert kill_switch.is_engaged()  # still engaged


# ---- /pause ---------------------------------------------------------------


def test_pause_engages_for_n_minutes():
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_pause(update, _make_ctx(args=["10"])))
    assert pause.is_active()
    assert 0 < pause.remaining_seconds() <= 600
    body = update.message.reply_text.call_args.args[0]
    assert "Paused for 10 min" in body
    assert "Resumes at" in body


def test_pause_with_no_args_shows_usage():
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_pause(update, _make_ctx(args=[])))
    assert not pause.is_active()
    body = update.message.reply_text.call_args.args[0]
    assert "Usage" in body


def test_pause_with_invalid_arg_shows_usage():
    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_pause(update, _make_ctx(args=["abc"])))
    assert not pause.is_active()


# ---- /journal -------------------------------------------------------------


def test_journal_reads_default_limit(monkeypatch):
    fake_entries = [
        {
            "ts": "2026-06-05T11:00:00+00:00",
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
        }
    ]
    from ictbot.portfolio import journal as journal_mod

    captured: dict = {}

    def fake_read(pair=None, limit=None):
        captured["limit"] = limit
        return fake_entries

    monkeypatch.setattr(journal_mod, "read_journal", fake_read)

    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_journal(update, _make_ctx(args=[])))
    assert captured["limit"] == 10  # default
    body = update.message.reply_text.call_args.args[0]
    assert "BTC" in body
    assert "BUY" in body


def test_journal_respects_custom_limit(monkeypatch):
    from ictbot.portfolio import journal as journal_mod

    captured: dict = {}
    monkeypatch.setattr(
        journal_mod,
        "read_journal",
        lambda pair=None, limit=None: captured.update(limit=limit) or [],
    )

    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_journal(update, _make_ctx(args=["25"])))
    assert captured["limit"] == 25


def test_journal_clamps_excessive_limit(monkeypatch):
    from ictbot.portfolio import journal as journal_mod

    captured: dict = {}
    monkeypatch.setattr(
        journal_mod,
        "read_journal",
        lambda pair=None, limit=None: captured.update(limit=limit) or [],
    )

    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_journal(update, _make_ctx(args=["9999"])))
    assert captured["limit"] == 50  # clamped


# ---- /status --------------------------------------------------------------


def test_status_calls_build_message(monkeypatch):
    from ictbot.notify import signal_check

    called: dict = {}

    def fake_build(*, pairs=None, full=False, **_kw):
        called["pairs"] = pairs
        called["full"] = full
        return "status-card-body"

    monkeypatch.setattr(signal_check, "build_message", fake_build)

    svc = _make_service(operator_user_id=42)
    update = _make_update(user_id=42)
    asyncio.run(svc._cmd_status(update, _make_ctx()))
    assert called["full"] is False
    update.message.reply_text.assert_called_once()
    body = update.message.reply_text.call_args.args[0]
    assert "status-card-body" in body
