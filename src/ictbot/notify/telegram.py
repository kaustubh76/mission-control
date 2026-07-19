"""
Telegram notification helper. Silently no-ops if token/chat_id are not set
so the analyzer can be used standalone for testing.

Fan-out: TELEGRAM_CHAT_ID is split on commas, so a single env var like
    TELEGRAM_CHAT_ID=2017826356,@ict_signals_public,-1001234567890
sends each message to every listed destination. Numeric chat IDs are
personal DMs / private groups; @-handles are public channel usernames;
-100… IDs are private channel/group chat IDs. The split-and-send order
is stable so a downstream parser sees the same arrival sequence per
cycle.

Failure semantics: returning True means at least one destination
accepted the message. Per-destination failures are logged so a stale
channel ID or a chat the bot has been kicked from doesn't silently
swallow heartbeats forever.

Network timeouts: requests.post(timeout=X) only enforces the read timeout
if X is a scalar — that lets a stuck IPv6 SYN_SENT eat several minutes
of scanner time before the kernel gives up. Passing a (connect, read)
tuple makes urllib3 enforce both separately, which is what we want when
the scanner heartbeat must come back fast or fail fast.
"""

import requests

from ictbot.settings import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN

_CONNECT_TIMEOUT_S = 5.0
_READ_TIMEOUT_S = 10.0


def _split_destinations(raw: str) -> list[str]:
    """Parse the (possibly comma-separated) chat-id env var into a list.

    Whitespace around each entry is trimmed so an env var written across
    visual padding still works. Empty entries (caused by stray commas)
    are dropped instead of triggering a bot-API call with chat_id="".
    """
    return [d.strip() for d in (raw or "").split(",") if d.strip()]


def send_telegram(message: str) -> bool:
    """Fan a message out to every chat id in TELEGRAM_CHAT_ID.

    Returns True if at least one destination accepted the message — same
    contract callers had when there was only one destination, so the
    scanner's success-vs-failure branching stays correct without
    change.
    """
    destinations = _split_destinations(TELEGRAM_CHAT_ID)
    if not TELEGRAM_TOKEN or not destinations:
        print("[telegram] skipped — TELEGRAM_TOKEN / TELEGRAM_CHAT_ID not set")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    any_ok = False
    for chat_id in destinations:
        try:
            r = requests.post(
                url,
                data={"chat_id": chat_id, "text": message},
                timeout=(_CONNECT_TIMEOUT_S, _READ_TIMEOUT_S),
            )
            r.raise_for_status()
            any_ok = True
        except Exception as e:
            # Don't let one bad destination block delivery to the rest —
            # this is the whole point of fanning out instead of crashing.
            print(f"[telegram] error sending to {chat_id!r}: {e}")
    return any_ok
