"""
Phase C — TG inline-button confirm-then-fire (legacy ictbot).

When TG_CONFIRM_MODE=true, every BUY/SELL signal the scanner produces
is DM'd to the operator (TG_OPERATOR_USER_ID) with two inline buttons:

    [✅ Trade NOW]  [❌ Skip]

The trade only fires if the operator clicks Trade within
TG_CONFIRM_TIMEOUT_S seconds. Click Skip → no fire, message edited to
"SKIPPED". Don't click → message edited to "EXPIRED ⏱" once the timeout
elapses. Clicks from any user other than TG_OPERATOR_USER_ID are
silently rejected.

Architecture:

  Scanner thread                 PTB daemon thread (asyncio event loop)
  ─────────────────              ───────────────────────────────────
  analyze_pair → BUY                                     │
  send_signal_with_buttons()  ───run_coroutine_threadsafe──▶ _send()
                                                          │   bot.send_message(buttons)
                                                          │   sleep(timeout) then _on_timeout()
                                                          │
                                ◀── _on_callback()  ◀── operator clicks Trade
                                       │
                                       └─ asyncio.to_thread(on_confirm, result)
                                                ▼
                                       router.route(result)  ← runs on worker thread,
                                                              same broker as scanner
                                                              would have used.

`python-telegram-bot` is an OPTIONAL dependency — the module imports
cleanly without it, only `TGConfirmService.__init__` raises if PTB
is missing. Activate via:

    pip install -e ".[tg]"
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


class SignalStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    SKIPPED = "SKIPPED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


@dataclass
class PendingSignal:
    """In-memory record of a signal awaiting operator confirmation.

    `result` is the analyzer's dict — kept as-is so on_confirm receives
    EXACTLY what would have gone to the router in non-confirm mode.
    """

    signal_id: str
    result: dict
    expires_at: datetime
    status: SignalStatus = SignalStatus.PENDING
    message_id: int | None = None
    chat_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def make_signal_id(result: dict) -> str:
    """Build the dedup key for a signal.

    Format: `{pair}|{closed_bar_minute}|{side}` — fits the 64-byte
    callback_data budget for every pair currently configured. Same bar
    fires the same id, so a refire on the next cycle collapses to one
    pending row (the existing send is left alone, no duplicate card).
    """
    pair = str(result.get("pair", "?"))
    side = str(result.get("entry", "?"))
    ts: str | None = None
    ltf_df = result.get("ltf_df")
    if ltf_df is not None:
        try:
            if len(ltf_df) >= 2:
                raw = ltf_df.iloc[-2]["time"]
                ts = raw.strftime("%Y%m%dT%H%M") if hasattr(raw, "strftime") else str(raw)[:16]
        except Exception:
            ts = None
    if not ts:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    return f"{pair}|{ts}|{side}"


class TGConfirmService:
    """Daemon-thread Telegram bot that orchestrates the confirm-then-fire flow.

    Lifecycle:
      svc = TGConfirmService(token, operator_user_id, timeout_s)
      svc.start(on_confirm=lambda r: router.route(r))
      # ... scanner runs, calls svc.send_signal_with_buttons(result) per BUY/SELL ...
      svc.stop()  # optional; daemon thread exits on process exit anyway

    Thread safety:
      The pending-signal dict is guarded by a Lock. Send/timeout/click
      coroutines all hop through it. on_confirm is invoked on a worker
      thread via asyncio.to_thread so ccxt is never called from the
      PTB event loop.
    """

    def __init__(
        self,
        token: str,
        operator_user_id: int,
        confirm_timeout_s: int = 180,
        enable_commands: bool = False,
    ) -> None:
        if not token:
            raise RuntimeError("TGConfirmService requires a Telegram bot token.")
        if not operator_user_id:
            raise RuntimeError(
                "TGConfirmService requires TG_OPERATOR_USER_ID. "
                "Get yours from @userinfobot on Telegram."
            )
        # Lazy import — PTB is optional. Only error when someone actually
        # constructs the service (i.e. TG_CONFIRM_MODE=true).
        try:
            from telegram.ext import Application, CallbackQueryHandler, CommandHandler
        except ImportError as exc:
            raise RuntimeError(
                "python-telegram-bot is not installed. "
                'Run `pip install -e ".[tg]"` to enable TG_CONFIRM_MODE.'
            ) from exc

        self._token = token
        self._operator_user_id = int(operator_user_id)
        self._timeout_s = int(confirm_timeout_s)
        self._pending: dict[str, PendingSignal] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app = None
        self._thread: threading.Thread | None = None
        self._on_confirm: Callable[[dict], Any] | None = None
        self._enable_commands = bool(enable_commands)
        self._Application = Application
        self._CallbackQueryHandler = CallbackQueryHandler
        self._CommandHandler = CommandHandler

    # ---- lifecycle ---------------------------------------------------------

    def start(self, on_confirm: Callable[[dict], Any]) -> None:
        """Spin up the PTB event loop on a daemon thread.

        `on_confirm(result)` is called when the operator clicks Trade.
        It runs on a worker thread (asyncio.to_thread), so it can do
        blocking ccxt work without freezing the bot's UI thread.
        """
        if self._thread is not None:
            raise RuntimeError("TGConfirmService.start() already called.")
        self._on_confirm = on_confirm
        self._thread = threading.Thread(target=self._run, daemon=True, name="tg-confirm")
        self._thread.start()
        # Wait briefly for the event loop to come up so the first
        # send_signal call doesn't drop on the floor.
        deadline = _time.monotonic() + 5.0
        while _time.monotonic() < deadline:
            if self._loop is not None:
                return
            _time.sleep(0.05)
        raise RuntimeError("TGConfirmService event loop didn't start within 5s.")

    def _run(self) -> None:
        """Daemon-thread body — owns the PTB event loop until process exit."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._app = self._Application.builder().token(self._token).build()
            self._app.add_handler(self._CallbackQueryHandler(self._on_callback))
            if self._enable_commands:
                Cmd = self._CommandHandler
                self._app.add_handler(Cmd("status", self._cmd_status))
                self._app.add_handler(Cmd("journal", self._cmd_journal))
                self._app.add_handler(Cmd("kill", self._cmd_kill))
                self._app.add_handler(Cmd("resume", self._cmd_resume))
                self._app.add_handler(Cmd("pause", self._cmd_pause))
                self._app.add_handler(Cmd("whoami", self._cmd_whoami))
                self._app.add_handler(Cmd("help", self._cmd_help))
                log.info("TGConfirmService operator-command handlers registered")
            log.info("TGConfirmService starting long-poll for operator %s", self._operator_user_id)
            self._app.run_polling(close_loop=False, stop_signals=None)
        except Exception as exc:
            log.exception("TGConfirmService event loop crashed: %s", exc)
        finally:
            self._loop = None

    def stop(self) -> None:
        """Best-effort shutdown. Mostly useful in tests; the daemon
        thread exits on process exit anyway."""
        if self._loop is not None and self._app is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._app.stop(), self._loop).result(timeout=5)
            except Exception as exc:
                log.warning("TGConfirmService stop failed: %s", exc)

    # ---- scanner-thread entry point ---------------------------------------

    def send_signal_with_buttons(self, result: dict) -> str:
        """Synchronous facade. Stores a PendingSignal, schedules the
        actual TG send + timeout watcher on the PTB loop, returns the
        signal_id. Returns immediately so the scanner can move on.

        Dedup: if the same (pair, closed_bar_minute, side) is already
        pending, no new card is sent — the existing one stays.
        """
        signal_id = make_signal_id(result)
        with self._lock:
            existing = self._pending.get(signal_id)
            if existing is not None and existing.status == SignalStatus.PENDING:
                log.info("send_signal: dedup hit for %s", signal_id)
                return signal_id
            self._pending[signal_id] = PendingSignal(
                signal_id=signal_id,
                result=result,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._timeout_s),
            )
        if self._loop is None:
            log.warning("send_signal called before loop ready; dropping %s", signal_id)
            return signal_id
        asyncio.run_coroutine_threadsafe(self._send_and_watch(signal_id), self._loop)
        return signal_id

    # ---- PTB-loop coroutines ----------------------------------------------

    async def _send_and_watch(self, signal_id: str) -> None:
        """Send the button card, then sleep until expiry. If still
        pending when the sleep returns, mark + edit message to EXPIRED.
        Other handlers (_on_callback) flip the status earlier."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        with self._lock:
            pending = self._pending.get(signal_id)
        if pending is None:
            return

        body = self._format_card(pending.result)
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Trade NOW", callback_data=f"cfm:{signal_id}"),
                    InlineKeyboardButton("❌ Skip", callback_data=f"skp:{signal_id}"),
                ]
            ]
        )
        try:
            sent = await self._app.bot.send_message(
                chat_id=self._operator_user_id,
                text=body,
                reply_markup=kb,
            )
        except Exception as exc:
            log.warning("send_message failed for %s: %s", signal_id, exc)
            with self._lock:
                self._pending.pop(signal_id, None)
            return

        with self._lock:
            p = self._pending.get(signal_id)
            if p is not None:
                p.message_id = sent.message_id
                p.chat_id = sent.chat_id

        # Sleep until expiry, then check & edit. Concurrent callbacks
        # flip status to EXECUTED/SKIPPED/FAILED first; we only act on
        # rows that are still PENDING.
        await asyncio.sleep(self._timeout_s)
        await self._on_timeout(signal_id)

    async def _on_callback(self, update, ctx) -> None:
        """Inline-button handler. Runs on the PTB event loop."""
        query = update.callback_query
        if query is None:
            return
        try:
            user_id = query.from_user.id if query.from_user else 0
            if user_id != self._operator_user_id:
                # Defence-in-depth: even if the button URL leaks, only
                # the configured operator can act on it. Reject silently
                # (no alert) to avoid leaking the bot's identity.
                await query.answer("Not authorised.", show_alert=False)
                log.info("rejected unauthorised click from user %s", user_id)
                return

            data = query.data or ""
            action, _, signal_id = data.partition(":")
            if not signal_id:
                await query.answer("malformed", show_alert=False)
                return

            with self._lock:
                pending = self._pending.get(signal_id)
            if pending is None or pending.status != SignalStatus.PENDING:
                await query.answer("Already actioned or expired.", show_alert=False)
                return

            if action == "cfm":
                await self._handle_confirm(query, pending)
            elif action == "skp":
                await self._handle_skip(query, pending)
            else:
                await query.answer("unknown action", show_alert=False)
        except Exception as exc:
            log.exception("callback handler crashed: %s", exc)

    async def _handle_confirm(self, query, pending: PendingSignal) -> None:
        """Run the operator's on_confirm callback on a worker thread.

        Worker thread = `asyncio.to_thread` — keeps ccxt's blocking I/O
        off the PTB event loop, so a slow exchange call doesn't freeze
        button clicks for other pairs.
        """
        await query.answer("Executing…", show_alert=False)
        try:
            outcome = await asyncio.to_thread(self._on_confirm, pending.result)
        except Exception as exc:
            log.exception("on_confirm raised for %s: %s", pending.signal_id, exc)
            with self._lock:
                pending.status = SignalStatus.FAILED
            await query.edit_message_text(
                self._format_card(pending.result) + f"\n\n⚠️ EXECUTION FAILED: {exc}"
            )
            return

        placed = bool(getattr(outcome, "placed", False)) if outcome is not None else False
        with self._lock:
            pending.status = SignalStatus.EXECUTED if placed else SignalStatus.FAILED
        if placed:
            suffix = "✅ EXECUTED"
        else:
            reason = getattr(outcome, "reason", "rejected") if outcome is not None else "no outcome"
            suffix = f"⚠️ REJECTED: {reason}"
        await query.edit_message_text(self._format_card(pending.result) + f"\n\n{suffix}")

    async def _handle_skip(self, query, pending: PendingSignal) -> None:
        with self._lock:
            pending.status = SignalStatus.SKIPPED
        await query.answer("Skipped.", show_alert=False)
        await query.edit_message_text(self._format_card(pending.result) + "\n\n❌ SKIPPED")

    async def _on_timeout(self, signal_id: str) -> None:
        """Fired when the configured timeout elapses. No-op if the row
        was already actioned."""
        with self._lock:
            pending = self._pending.get(signal_id)
            if pending is None or pending.status != SignalStatus.PENDING:
                return
            pending.status = SignalStatus.EXPIRED

        if pending.message_id and pending.chat_id:
            try:
                await self._app.bot.edit_message_text(
                    chat_id=pending.chat_id,
                    message_id=pending.message_id,
                    text=self._format_card(pending.result) + "\n\n⏱ EXPIRED",
                )
            except Exception as exc:
                log.warning("expiry edit failed for %s: %s", signal_id, exc)

    # ---- helpers -----------------------------------------------------------

    def _format_card(self, result: dict) -> str:
        """Format the signal card. Reuses signal_check._pair_block so the
        button card matches the heartbeat card format exactly."""
        try:
            from ictbot.notify.signal_check import _pair_block

            return _pair_block(result)
        except Exception:
            # Defensive fallback so a formatting bug doesn't lose signals.
            pair = result.get("pair", "?")
            side = result.get("entry", "?")
            return (
                f"{side} {pair}\n"
                f"price {result.get('price')}\n"
                f"SL {result.get('sl')}  TP {result.get('tp')}\n"
                f"conf {result.get('confidence', 0)}%"
            )

    # ---- test/introspection accessors -------------------------------------

    def get_pending(self, signal_id: str) -> PendingSignal | None:
        """Test/CLI accessor — never used by production code paths."""
        with self._lock:
            return self._pending.get(signal_id)

    # ---- Phase D: TG operator commands ------------------------------------
    # Each handler:
    #   1. Drops silently if the sender is not the operator (defence in
    #      depth — even if the bot's @handle leaks, only the operator
    #      can drive it).
    #   2. Replies via `update.message.reply_text` so the response lands
    #      in the same chat the operator sent the command from.
    # All handlers swallow their own exceptions so a misformed command
    # never crashes the PTB event loop.

    def _is_operator(self, update) -> bool:
        user = getattr(update, "effective_user", None)
        user_id = getattr(user, "id", 0) or 0
        return int(user_id) == self._operator_user_id

    async def _cmd_status(self, update, ctx) -> None:
        if not self._is_operator(update):
            return
        try:
            from ictbot.notify.signal_check import build_message
            from ictbot.settings import PAIRS

            text = build_message(pairs=list(PAIRS), full=False)
        except Exception as exc:
            log.warning("/status build_message failed: %s", exc)
            text = f"⚠ /status failed: {exc}"
        await update.message.reply_text(text[:4000])  # TG hard cap = 4096

    async def _cmd_journal(self, update, ctx) -> None:
        if not self._is_operator(update):
            return
        args = getattr(ctx, "args", None) or []
        try:
            limit = int(args[0]) if args else 10
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))
        try:
            from ictbot.portfolio.journal import read_journal, score_journal

            entries = read_journal(limit=limit)
            text = self._format_journal(entries, limit=limit, score=score_journal(entries))
        except Exception as exc:
            log.warning("/journal failed: %s", exc)
            text = f"⚠ /journal failed: {exc}"
        await update.message.reply_text(text[:4000])

    async def _cmd_kill(self, update, ctx) -> None:
        if not self._is_operator(update):
            return
        args = getattr(ctx, "args", None) or []
        reason = " ".join(args).strip() or "tg-operator"
        try:
            from ictbot.runtime import kill_switch

            kill_switch.engage(reason=reason)
            text = (
                f"🛑 KILL SWITCH ENGAGED\n"
                f"reason: {reason}\n"
                f"Live trading halted until `/resume yes` AND a manual\n"
                f"ENABLE_LIVE_TRADING=true in .env + restart."
            )
        except Exception as exc:
            log.exception("/kill failed: %s", exc)
            text = f"⚠ /kill failed: {exc}"
        await update.message.reply_text(text)

    async def _cmd_resume(self, update, ctx) -> None:
        if not self._is_operator(update):
            return
        args = getattr(ctx, "args", None) or []
        arg = (args[0] if args else "").strip().lower()
        if arg != "yes":
            await update.message.reply_text(
                "Usage: /resume yes\n"
                "Releases the kill switch + pause. Does NOT re-enable\n"
                "ENABLE_LIVE_TRADING — that stays a manual .env edit + restart."
            )
            return
        try:
            from ictbot.runtime import kill_switch, pause

            kill_switch.release()
            pause.release()
            text = (
                "✅ Kill switch + pause cleared.\n"
                "To resume live trading, set ENABLE_LIVE_TRADING=true in .env\n"
                "and restart the scanner."
            )
        except Exception as exc:
            log.exception("/resume failed: %s", exc)
            text = f"⚠ /resume failed: {exc}"
        await update.message.reply_text(text)

    async def _cmd_pause(self, update, ctx) -> None:
        if not self._is_operator(update):
            return
        args = getattr(ctx, "args", None) or []
        try:
            minutes = int(args[0]) if args else 0
        except (TypeError, ValueError):
            minutes = 0
        if minutes <= 0:
            await update.message.reply_text(
                "Usage: /pause <minutes>\n"
                "Halts evaluation for N minutes. Auto-resumes when expired."
            )
            return
        try:
            from ictbot.runtime import pause

            until = pause.engage(seconds=minutes * 60)
            text = (
                f"⏸  Paused for {minutes} min.\nResumes at {until.strftime('%Y-%m-%d %H:%M UTC')}."
            )
        except Exception as exc:
            log.exception("/pause failed: %s", exc)
            text = f"⚠ /pause failed: {exc}"
        await update.message.reply_text(text)

    async def _cmd_whoami(self, update, ctx) -> None:
        # No operator-only guard — anyone DM'ing the bot can confirm
        # whether they're the configured operator. The reply itself
        # is useful diagnostic info, not a privilege.
        user = getattr(update, "effective_user", None)
        you = getattr(user, "id", 0) or 0
        match = "✅" if int(you) == self._operator_user_id else "❌"
        await update.message.reply_text(
            f"operator_id = {self._operator_user_id}\nyou        = {you}\nmatch      = {match}"
        )

    async def _cmd_help(self, update, ctx) -> None:
        if not self._is_operator(update):
            return
        await update.message.reply_text(
            "Operator commands:\n"
            "/status         current per-pair signal cards\n"
            "/journal [n]    last n signals (default 10, max 50)\n"
            "/kill <reason>  engage kill switch + halt live trading\n"
            "/resume yes     clear kill switch + pause (does NOT re-enable live)\n"
            "/pause <min>    halt evaluation for N minutes\n"
            "/whoami         show operator id vs your id\n"
            "/help           this message"
        )

    @staticmethod
    def _format_journal(entries: list, *, limit: int, score: dict) -> str:
        """Compact journal table. Mirrors cli.journal_cmd's marker shape
        so the operator sees the same WIN/LOSS/OPEN glyphs as the CLI."""
        if not entries:
            return "📒 journal empty"
        lines = [f"📒 last {min(limit, len(entries))} signals"]
        for e in entries:
            outcome = e.get("outcome", "?")
            marker = {"WIN": "✓", "LOSS": "✗", "OPEN": "·"}.get(outcome, "?")
            ts = (e.get("ts") or "")[:16].replace("T", " ")
            entry = e.get("entry", "?")
            pair = e.get("pair", "?").split("/")[0]
            price = e.get("price")
            conf = e.get("confidence", 0)
            lines.append(f"{marker} {ts} {pair} {entry} {price} c{conf}")
        wr = score.get("win_rate")
        wr_str = f"{wr:.0f}%" if isinstance(wr, (int, float)) else "—"
        lines.append(
            f"\ntotals: {score.get('total', 0)} sigs · "
            f"wins {score.get('wins', 0)} · losses {score.get('losses', 0)} · "
            f"open {score.get('open', 0)} · win-rate {wr_str}"
        )
        return "\n".join(lines)
