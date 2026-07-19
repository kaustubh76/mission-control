"""
Telegram session gate — decides whether a heartbeat or near-miss
message is worth sending right now, and prepends an off-session
disclaimer when a high-confidence setup overrides the gate.

The ICT trading edge concentrates in the London + New York killzones;
sending heartbeats during dead hours just trains the reader to ignore
the channel. The gate's job is to keep silence outside session UNLESS
something with a high confidence number breaks through, in which case
the message arrives with a clear "off-session — handle with care"
banner so nobody mistakes it for a normal in-session alert.

Pure function so it's trivial to test and reuse across send sites
(scanner heartbeat, near-miss alerts, future flows).
"""

from __future__ import annotations

OFF_SESSION_DISCLAIMER = (
    "⚠️ OFF-SESSION ALERT\n"
    "No London / NY killzone is open right now. Liquidity is thinner,\n"
    "slippage and false breaks are more likely. The bot only sent this\n"
    "because confidence cleared the off-session bypass threshold.\n"
    "Treat as informational — consider waiting for the next killzone.\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
)


def decide_notify(
    *,
    in_session: bool,
    confidence: int,
    in_session_only: bool = True,
    min_confidence_bypass: int = 100,
) -> tuple[bool, str]:
    """Return (should_send, prefix).

    Behaviour matrix:
        in_session_only=False               → always send, no prefix
        in_session=True                     → always send, no prefix
        in_session=False & conf >= bypass   → send, off-session prefix
        in_session=False & conf <  bypass   → suppress

    Args:
        in_session:            killzone is currently open.
        confidence:            the result's confidence (0..100). For
                               aggregated payloads (heartbeat card
                               pack), pass the MAX confidence across
                               pairs so a single high-conviction setup
                               still gets through.
        in_session_only:       master gate; False = legacy unconditional.
        min_confidence_bypass: bypass threshold (default 100 = perfect
                               setup only).

    Returns:
        (True,  "")             — send as-is.
        (True,  prefix)         — send with the off-session disclaimer
                                  prepended.
        (False, "")             — suppress.
    """
    if not in_session_only:
        return True, ""
    if in_session:
        return True, ""
    if confidence >= min_confidence_bypass:
        return True, OFF_SESSION_DISCLAIMER
    return False, ""
