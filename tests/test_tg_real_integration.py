"""
Phase C — opt-in real-Telegram integration test.

Hits the real api.telegram.org. Skipped by default so CI doesn't burn
TG message quota and tests don't fail without a configured bot.

Activate with:
    RUN_TG_INTEGRATION=1 pytest -q tests/test_tg_real_integration.py

What it verifies (that mocks can't):
  - The PTB v22 daemon thread actually starts and the event loop comes up.
  - run_coroutine_threadsafe → bot.send_message + InlineKeyboardMarkup
    survive a real round-trip to Telegram's bot API.
  - The returned message_id propagates into the PendingSignal record.
  - The configured bot token is valid (getMe).
"""

from __future__ import annotations

import os
import time

import pytest

# Skip the whole module unless explicitly opted in.
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TG_INTEGRATION") != "1",
    reason="set RUN_TG_INTEGRATION=1 to hit the real Telegram API",
)


def test_real_send_round_trip():
    """Send one card to the operator's DM, assert message_id arrives."""
    from ictbot.notify.tg_confirm import TGConfirmService
    from ictbot.settings import settings

    if not settings.telegram_token or not settings.tg_operator_user_id:
        pytest.skip("TELEGRAM_TOKEN / TG_OPERATOR_USER_ID not configured")

    svc = TGConfirmService(
        token=settings.telegram_token,
        operator_user_id=settings.tg_operator_user_id,
        confirm_timeout_s=60,
    )
    svc.start(on_confirm=lambda r: None)

    fake = {
        "pair": "BTC/USDT:USDT",
        "entry": "BUY",
        "price": 63500.0,
        "sl": 62900.0,
        "tp": 65300.0,
        "tp2": 0.0,
        "rr": 3.0,
        "confidence": 100,
        "htf_bias": "BULLISH",
        "ltf_bias": "BULLISH",
        "poi_tap": "POI TAPPED",
        "ltf_mss": "BULLISH MSS",
        "fvg": "BULLISH FVG",
        "proposed_direction": "BUY",
        "proposed_sl": 62900.0,
        "proposed_tp": 65300.0,
    }
    sid = svc.send_signal_with_buttons(fake)

    deadline = time.monotonic() + 10
    mid = None
    while time.monotonic() < deadline:
        p = svc.get_pending(sid)
        if p is not None and p.message_id is not None:
            mid = p.message_id
            break
        time.sleep(0.1)

    assert mid is not None, "TG send_message returned no message_id within 10s"
    assert isinstance(mid, int) and mid > 0
    # signal_id is the pair|minute|side composite — must contain the pair.
    assert "BTC/USDT:USDT" in sid


def test_bot_token_valid():
    """getMe round-trip — proves the token is current and the bot is alive."""
    import requests

    from ictbot.settings import settings

    if not settings.telegram_token:
        pytest.skip("TELEGRAM_TOKEN not configured")
    r = requests.get(
        f"https://api.telegram.org/bot{settings.telegram_token}/getMe",
        timeout=10,
    )
    body = r.json()
    assert body.get("ok") is True, f"getMe failed: {body}"
    assert body["result"]["is_bot"] is True
