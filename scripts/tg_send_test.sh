#!/usr/bin/env bash
# Phase C — non-interactive TG send-only smoke.
#
# Posts ONE button card to the operator's DM via the real PTB + real
# Telegram API, prints the message_id, then exits in <5s without
# waiting for a click. Use this to prove the wiring works.
#
# Distinct from tg_test_signal.sh: that script blocks waiting for the
# operator to click and runs the result through a PaperBroker. This
# one just sends and exits — useful for CI / quick proof of life.
#
# Prereqs (set in .env):
#   TELEGRAM_TOKEN
#   TG_OPERATOR_USER_ID  — your numeric Telegram user id
#
# Expected console output:
#   ✅ card sent, message_id=<int> signal_id=<str>
#
# Expected TG outcome:
#   Operator's DM receives a signal card with two inline buttons.
#   Clicking does nothing — no on_confirm wired in send-only mode.

set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source .venv/bin/activate

python - <<'PY'
import os, sys, time
# Force-reload settings so a fresh .env value (TG_OPERATOR_USER_ID) is picked up.
from importlib import reload
from ictbot import settings as s
reload(s)

if not s.settings.telegram_token:
    print("❌ TELEGRAM_TOKEN missing in .env", file=sys.stderr); sys.exit(1)
if not s.settings.tg_operator_user_id:
    print("❌ TG_OPERATOR_USER_ID missing in .env", file=sys.stderr); sys.exit(1)

from ictbot.notify.tg_confirm import TGConfirmService, SignalStatus

svc = TGConfirmService(
    token=s.settings.telegram_token,
    operator_user_id=s.settings.tg_operator_user_id,
    confirm_timeout_s=60,
)
# No on_confirm — clicking will dispatch but the handler will raise
# because the callback is None. That's fine for a send-only smoke;
# we only care that the card lands.
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

# Poll up to 5s for the send coroutine to populate message_id.
deadline = time.monotonic() + 5
mid = None
while time.monotonic() < deadline:
    p = svc.get_pending(sid)
    if p is not None and p.message_id is not None:
        mid = p.message_id
        break
    time.sleep(0.1)

if mid is None:
    print(f"⚠️  card NOT delivered within 5s (signal_id={sid})", file=sys.stderr)
    sys.exit(2)

print(f"✅ card sent, message_id={mid} signal_id={sid}")
PY
