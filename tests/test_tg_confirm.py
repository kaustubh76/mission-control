"""
Phase C — TGConfirmService tests (docs/autotrade_plan.md).

All TG and PTB interactions are mocked. No network. The fake-signal
smoke (scripts/tg_test_signal.sh) is the integration sanity check.

Coverage targets:
  (a) Confirm within timeout → on_confirm called once with stored dict.
  (b) Skip → on_confirm never called, message edited.
  (c) Timeout → on_confirm never called, status = EXPIRED.
  (d) Wrong user_id → silent reject, on_confirm not called.
  (e) Duplicate signal_id within window collapses to one pending row.
  (f) Lazy import — TGConfirmService raises a clean RuntimeError when
      python-telegram-bot isn't installed.
  (g) Startup validation — TG_CONFIRM_MODE=true with operator_user_id=0
      refuses to construct.
  (h) signal_id encoding fits Telegram's 64-byte callback_data budget.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from ictbot.notify.tg_confirm import (
    PendingSignal,
    SignalStatus,
    TGConfirmService,
    make_signal_id,
)

# ---- helpers ---------------------------------------------------------------


def _result(pair="BTC/USDT:USDT", side="BUY", price=100.0):
    """Minimal-but-complete result dict the scanner would pass."""
    return {
        "pair": pair,
        "entry": side,
        "price": price,
        "sl": 99.0,
        "tp": 103.0,
        "rr": 3.0,
        "confidence": 100,
    }


def _make_service(operator_user_id=42, timeout_s=180, on_confirm=None):
    """Construct without start() — we drive the async methods directly."""
    svc = TGConfirmService(
        token="test:fake",
        operator_user_id=operator_user_id,
        confirm_timeout_s=timeout_s,
    )
    svc._on_confirm = on_confirm or MagicMock()
    svc._app = MagicMock()
    svc._app.bot = MagicMock()
    svc._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=999, chat_id=42))
    svc._app.bot.edit_message_text = AsyncMock()
    return svc


def _make_query(callback_data, user_id=42):
    """Build a mock CallbackQuery PTB would pass to the handler."""
    q = MagicMock()
    q.data = callback_data
    q.from_user = MagicMock(id=user_id)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.text = "ORIGINAL TEXT"
    update = MagicMock()
    update.callback_query = q
    return update, q


# ---- (h) signal_id encoding fits TG's 64-byte budget ------------------------


@pytest.mark.parametrize(
    "pair",
    [
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
        "XRP/USDT:USDT",
        "PEPE1000/USDT:USDT",  # forward-compat: long meme tickers
    ],
)
def test_signal_id_fits_callback_data_budget(pair):
    """Telegram bot API caps callback_data at 64 bytes. With the 'cfm:'
    prefix the encoded id must stay under that."""
    sid = make_signal_id(_result(pair=pair))
    encoded = f"cfm:{sid}".encode()
    assert len(encoded) <= 64, f"signal_id too long: {len(encoded)} bytes"


def test_signal_id_dedups_same_bar():
    r1 = _result()
    r2 = _result()  # same pair/side, no ltf_df → uses wall-clock minute
    # In the same minute (overwhelmingly likely in a unit test), the ids
    # match — that's the desired dedup.
    assert make_signal_id(r1) == make_signal_id(r2)


# ---- (g) startup validation -------------------------------------------------


def test_refuses_construction_without_operator_id():
    with pytest.raises(RuntimeError, match="TG_OPERATOR_USER_ID"):
        TGConfirmService(token="x", operator_user_id=0)


def test_refuses_construction_without_token():
    with pytest.raises(RuntimeError, match="bot token"):
        TGConfirmService(token="", operator_user_id=42)


def test_settings_refuse_when_mode_on_with_no_operator(monkeypatch):
    """The settings.py block at module-import time should refuse to load
    Settings() when TG_CONFIRM_MODE=true and TG_OPERATOR_USER_ID is unset.

    We force TG_OPERATOR_USER_ID=0 explicitly (rather than delenv-ing)
    because the real `.env` may contain a populated value — env vars
    take precedence over `.env`, so setting it to "0" reliably
    overrides whatever's on disk.
    """
    monkeypatch.setenv("TG_CONFIRM_MODE", "true")
    monkeypatch.setenv("TG_OPERATOR_USER_ID", "0")
    sys.modules.pop("ictbot.settings", None)
    with pytest.raises(RuntimeError, match="TG_OPERATOR_USER_ID"):
        import ictbot.settings  # noqa: F401
    # Restore env so other tests in this process aren't affected.
    monkeypatch.delenv("TG_CONFIRM_MODE", raising=False)
    monkeypatch.delenv("TG_OPERATOR_USER_ID", raising=False)
    sys.modules.pop("ictbot.settings", None)
    import ictbot.settings  # noqa: F401  — reset back to clean state


# ---- (f) lazy import of python-telegram-bot --------------------------------


def test_lazy_import_raises_clean_error_when_ptb_missing(monkeypatch):
    """Even when PTB IS installed in the dev env, simulate the absent-PTB
    path by monkey-patching the import to raise."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "telegram.ext":
            raise ImportError("simulated: telegram.ext not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="python-telegram-bot is not installed"):
        TGConfirmService(token="x", operator_user_id=42)


# ---- (a) confirm within timeout --------------------------------------------


@pytest.mark.asyncio
async def test_confirm_invokes_on_confirm_once_with_stored_dict():
    confirm_cb = MagicMock(return_value=MagicMock(placed=True, reason="ok"))
    svc = _make_service(on_confirm=confirm_cb)

    sid = make_signal_id(_result())
    svc._pending[sid] = PendingSignal(
        signal_id=sid,
        result=_result(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=180),
    )

    update, query = _make_query(f"cfm:{sid}", user_id=42)
    await svc._on_callback(update, None)

    confirm_cb.assert_called_once()
    # The callback must receive the EXACT stored result dict.
    passed = confirm_cb.call_args.args[0]
    assert passed["pair"] == "BTC/USDT:USDT"
    assert passed["entry"] == "BUY"

    assert svc._pending[sid].status == SignalStatus.EXECUTED
    # Message edited to include EXECUTED suffix.
    query.edit_message_text.assert_called_once()
    edited_text = query.edit_message_text.call_args.args[0]
    assert "EXECUTED" in edited_text


@pytest.mark.asyncio
async def test_confirm_rejected_outcome_marks_failed():
    """If router.route returns placed=False (cap rejection), mark FAILED
    and show the rejection reason in the edited message."""
    confirm_cb = MagicMock(return_value=MagicMock(placed=False, reason="daily_loss_limit"))
    svc = _make_service(on_confirm=confirm_cb)

    sid = make_signal_id(_result())
    svc._pending[sid] = PendingSignal(
        signal_id=sid,
        result=_result(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=180),
    )

    update, query = _make_query(f"cfm:{sid}", user_id=42)
    await svc._on_callback(update, None)

    assert svc._pending[sid].status == SignalStatus.FAILED
    edited_text = query.edit_message_text.call_args.args[0]
    assert "REJECTED" in edited_text
    assert "daily_loss_limit" in edited_text


@pytest.mark.asyncio
async def test_confirm_callback_exception_marks_failed():
    """An exception inside on_confirm (e.g. ccxt blew up) is caught,
    the pending row is marked FAILED, and the message shows the error.
    Critical: the PTB handler MUST NOT crash — that would kill the bot."""
    confirm_cb = MagicMock(side_effect=RuntimeError("ccxt explosion"))
    svc = _make_service(on_confirm=confirm_cb)

    sid = make_signal_id(_result())
    svc._pending[sid] = PendingSignal(
        signal_id=sid,
        result=_result(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=180),
    )

    update, query = _make_query(f"cfm:{sid}", user_id=42)
    await svc._on_callback(update, None)  # must not raise

    assert svc._pending[sid].status == SignalStatus.FAILED
    edited_text = query.edit_message_text.call_args.args[0]
    assert "FAILED" in edited_text


# ---- (b) skip ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_does_not_call_on_confirm():
    confirm_cb = MagicMock()
    svc = _make_service(on_confirm=confirm_cb)

    sid = make_signal_id(_result())
    svc._pending[sid] = PendingSignal(
        signal_id=sid,
        result=_result(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=180),
    )

    update, query = _make_query(f"skp:{sid}", user_id=42)
    await svc._on_callback(update, None)

    confirm_cb.assert_not_called()
    assert svc._pending[sid].status == SignalStatus.SKIPPED
    edited_text = query.edit_message_text.call_args.args[0]
    assert "SKIPPED" in edited_text


# ---- (d) wrong user_id ------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_user_silently_rejected():
    confirm_cb = MagicMock()
    svc = _make_service(operator_user_id=42, on_confirm=confirm_cb)

    sid = make_signal_id(_result())
    svc._pending[sid] = PendingSignal(
        signal_id=sid,
        result=_result(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=180),
    )

    update, query = _make_query(f"cfm:{sid}", user_id=99)  # different user
    await svc._on_callback(update, None)

    confirm_cb.assert_not_called()
    assert svc._pending[sid].status == SignalStatus.PENDING  # untouched
    # answer() called to dismiss the click, but no message edit.
    query.answer.assert_called_once()
    query.edit_message_text.assert_not_called()


# ---- (e) duplicate signal_id collapses --------------------------------------


def test_duplicate_signal_id_does_not_overwrite_pending(monkeypatch):
    """A second send_signal_with_buttons for the same id (e.g. the bar
    refires on the next cycle) must NOT replace the existing pending
    row — that would lose the operator's outstanding decision window."""
    svc = _make_service()
    # Skip start() — install a fake "loop ready" so send_signal proceeds
    # past the not-ready guard. We close any scheduled coroutine to
    # silence the "coroutine never awaited" warning.
    svc._loop = MagicMock()

    def consume_coro(coro, loop):
        coro.close()
        return MagicMock()

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", consume_coro)

    sid_first = svc.send_signal_with_buttons(_result())
    first_pending = svc._pending[sid_first]

    sid_second = svc.send_signal_with_buttons(_result())
    assert sid_first == sid_second
    assert svc._pending[sid_first] is first_pending  # SAME object — no overwrite


# ---- (c) timeout ------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_timeout_marks_expired_when_still_pending():
    confirm_cb = MagicMock()
    svc = _make_service(on_confirm=confirm_cb)

    sid = make_signal_id(_result())
    svc._pending[sid] = PendingSignal(
        signal_id=sid,
        result=_result(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=180),
        message_id=999,
        chat_id=42,
    )

    await svc._on_timeout(sid)

    assert svc._pending[sid].status == SignalStatus.EXPIRED
    confirm_cb.assert_not_called()
    svc._app.bot.edit_message_text.assert_awaited_once()
    edit_kwargs = svc._app.bot.edit_message_text.call_args.kwargs
    assert "EXPIRED" in edit_kwargs["text"]


@pytest.mark.asyncio
async def test_on_timeout_noop_when_already_actioned():
    """If the operator already clicked Trade or Skip, the timeout sleeper
    must NOT overwrite the resolution. Race-safety check."""
    svc = _make_service()
    sid = make_signal_id(_result())
    svc._pending[sid] = PendingSignal(
        signal_id=sid,
        result=_result(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=180),
        status=SignalStatus.EXECUTED,  # already actioned
        message_id=999,
        chat_id=42,
    )

    await svc._on_timeout(sid)

    assert svc._pending[sid].status == SignalStatus.EXECUTED  # unchanged
    svc._app.bot.edit_message_text.assert_not_awaited()


# ---- handler robustness -----------------------------------------------------


@pytest.mark.asyncio
async def test_callback_on_missing_pending_row():
    """If the operator clicks Trade on a row that's no longer pending
    (race: timeout fired between the click and the handler), the
    handler must answer politely and not crash."""
    svc = _make_service()
    update, query = _make_query("cfm:nonexistent-id", user_id=42)
    await svc._on_callback(update, None)
    query.answer.assert_called_once()  # acknowledges the click
    query.edit_message_text.assert_not_called()  # but doesn't edit


@pytest.mark.asyncio
async def test_callback_on_malformed_data():
    svc = _make_service()
    update, query = _make_query("malformed-no-colon", user_id=42)
    await svc._on_callback(update, None)
    # The string "malformed-no-colon".partition(":") returns
    # ("malformed-no-colon", "", "") — signal_id is empty, so we answer
    # "malformed" and return.
    query.answer.assert_called_once()
    query.edit_message_text.assert_not_called()


# ---- pytest-asyncio mode ---------------------------------------------------
# Without the marker, pytest-asyncio (which we depend on transitively via
# python-telegram-bot's test deps) needs explicit hint.


def pytest_collection_modifyitems(config, items):
    """Apply asyncio mark to every async test in this file."""
    for item in items:
        if asyncio.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.asyncio)
