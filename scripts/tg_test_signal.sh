#!/usr/bin/env bash
# Phase C — fake-signal smoke for the TG confirm-then-fire flow.
#
# Injects a synthetic BUY card into the TGConfirmService against a
# PaperBroker and waits for the operator to click. NO REAL ORDER is
# placed regardless of ENABLE_LIVE_TRADING — see the safety note inside
# the python script below.
#
# Usage:
#   bash scripts/tg_test_signal.sh                 # synthetic BUY @ 100
#   bash scripts/tg_test_signal.sh SELL            # synthetic SELL @ 100
#   bash scripts/tg_test_signal.sh BUY 63500       # custom price
#
# Prereqs (set in .env):
#   TELEGRAM_TOKEN
#   TG_OPERATOR_USER_ID  — your numeric Telegram user id (from @userinfobot)
#
# What you'll see:
#   1. Console prints the synthetic signal it's about to send.
#   2. Within ~2s, the operator gets a TG DM with a signal card and
#      [✅ Trade NOW] [❌ Skip] buttons.
#   3. Click Trade → console prints `placed=True` + the paper-broker
#      order id. Message edits to "EXECUTED".
#      Click Skip → console prints `SKIPPED`. Message edits to "SKIPPED".
#      Wait 60s → console prints `EXPIRED`. Message edits to "EXPIRED ⏱".
#
# Safety: PaperBroker is hard-coded. Even with ENABLE_LIVE_TRADING=true
# in your env, this script will NOT place a real order. To do a real
# live test, use the normal scanner with TG_CONFIRM_MODE=true.

set -euo pipefail
cd "$(dirname "$0")/.."

SIDE="${1:-BUY}"
PRICE="${2:-100.0}"

# shellcheck disable=SC1091
source .venv/bin/activate

python - <<PY
import os, sys, time
os.environ["TG_CONFIRM_MODE"] = "true"
# Force-reload settings against the patched env.
from importlib import reload
from ictbot import settings as s
reload(s)

if not s.settings.telegram_token:
    print("❌ TELEGRAM_TOKEN missing in .env", file=sys.stderr); sys.exit(1)
if not s.settings.tg_operator_user_id:
    print("❌ TG_OPERATOR_USER_ID missing in .env (message @userinfobot to get yours)", file=sys.stderr); sys.exit(1)

print(f"   operator user id: {s.settings.tg_operator_user_id}")
print(f"   timeout:          {s.settings.tg_confirm_timeout_s}s")
print(f"   side:             ${SIDE}")
print(f"   price:            ${PRICE}")
print()

# Always PaperBroker — safety guarantee documented in the script header.
from ictbot.exec.paper import PaperBroker
from ictbot.orchestrator.router import SignalRouter
from ictbot.portfolio.account import Account
from ictbot.portfolio.caps import CapGate, DailyLossLimit, MaxDrawdown, MaxOpenPositions
from ictbot.portfolio.journal import append_signal
from ictbot.notify.tg_confirm import TGConfirmService

paper = PaperBroker(starting_balance=10_000.0)
account = Account(starting_balance=10_000.0)
caps = CapGate([MaxOpenPositions(1), DailyLossLimit(limit_R=1.0), MaxDrawdown(account=account, limit=0.05)])
router = SignalRouter(broker=paper, cap_gate=caps, balance=10_000.0, risk_pct=0.005, journal=append_signal, account=account)

svc = TGConfirmService(
    token=s.settings.telegram_token,
    operator_user_id=s.settings.tg_operator_user_id,
    confirm_timeout_s=min(s.settings.tg_confirm_timeout_s, 60),  # cap to 60s for the smoke
)

outcome_holder = {}

def on_confirm(result):
    print(f"⚡ on_confirm fired — calling router.route(paper)…")
    o = router.route(result)
    outcome_holder["o"] = o
    return o

svc.start(on_confirm=on_confirm)
print("✅ TGConfirmService started, sending card…")

side = "${SIDE}"
price = float("${PRICE}")
fake_signal = {
    "pair": "BTC/USDT:USDT",
    "entry": side,
    "price": price,
    "sl": price * (0.99 if side == "BUY" else 1.01),
    "tp": price * (1.03 if side == "BUY" else 0.97),
    "tp2": 0.0,
    "rr": 3.0,
    "confidence": 100,
    "htf_bias": "BULLISH" if side == "BUY" else "BEARISH",
    "ltf_bias": "BULLISH" if side == "BUY" else "BEARISH",
    "poi_tap": "POI TAPPED",
    "ltf_mss": "BULLISH MSS" if side == "BUY" else "BEARISH MSS",
    "fvg": "BULLISH FVG" if side == "BUY" else "BEARISH FVG",
    "proposed_direction": side,
    "proposed_sl": price * (0.99 if side == "BUY" else 1.01),
    "proposed_tp": price * (1.03 if side == "BUY" else 0.97),
}

sid = svc.send_signal_with_buttons(fake_signal)
print(f"   signal_id: {sid}")
print()
print("Now check your Telegram DM and click a button.")
print("(this script will print the result and exit when you do)")

from ictbot.notify.tg_confirm import SignalStatus
deadline = time.monotonic() + min(s.settings.tg_confirm_timeout_s, 60) + 5
while time.monotonic() < deadline:
    p = svc.get_pending(sid)
    if p is not None and p.status != SignalStatus.PENDING:
        print()
        print(f"📥 final status: {p.status.value}")
        if "o" in outcome_holder:
            o = outcome_holder["o"]
            print(f"   router.outcome.placed: {o.placed}")
            if o.placed:
                print(f"   paper order: {o.order.side} {o.order.pair} entry={o.order.entry} qty={o.order.qty}")
            elif o.rejection:
                print(f"   rejected by cap: {o.rejection.reason}")
        sys.exit(0)
    time.sleep(0.5)

print("⏱ timed out waiting for click — final status:", svc.get_pending(sid).status.value)
PY
