"""
TG session gate: heartbeats and near-miss alerts fire only during
London / NY killzones, except when confidence clears the bypass
threshold — then they fire with an off-session disclaimer prepended.

Two layers under test:
  1. `decide_notify` pure function — every cell of the behaviour matrix.
  2. Settings defaults (`tg_in_session_only=True`, bypass=100) are what
     the rest of the code reads, so a regression that flips them
     accidentally would surface here.
"""

from __future__ import annotations

from ictbot.runtime.session_gate import OFF_SESSION_DISCLAIMER, decide_notify

# --- pure-function matrix ----------------------------------------------------


def test_in_session_always_sends_without_prefix():
    send, prefix = decide_notify(in_session=True, confidence=0)
    assert send is True
    assert prefix == ""


def test_in_session_with_high_confidence_no_prefix():
    """Inside session, confidence doesn't matter — no off-session banner."""
    send, prefix = decide_notify(in_session=True, confidence=100)
    assert send is True
    assert prefix == ""


def test_off_session_below_bypass_suppresses():
    send, prefix = decide_notify(in_session=False, confidence=75, min_confidence_bypass=100)
    assert send is False
    assert prefix == ""


def test_off_session_at_bypass_sends_with_disclaimer():
    """Exactly at the threshold = bypass fires (>= comparison)."""
    send, prefix = decide_notify(in_session=False, confidence=100, min_confidence_bypass=100)
    assert send is True
    assert prefix == OFF_SESSION_DISCLAIMER


def test_off_session_above_bypass_sends_with_disclaimer():
    send, prefix = decide_notify(in_session=False, confidence=120, min_confidence_bypass=100)
    assert send is True
    assert prefix == OFF_SESSION_DISCLAIMER


def test_lower_bypass_lets_more_through():
    """Operators who want chattier off-session output can lower the bar."""
    send, prefix = decide_notify(in_session=False, confidence=75, min_confidence_bypass=75)
    assert send is True
    assert prefix == OFF_SESSION_DISCLAIMER


def test_gate_off_means_always_send_no_prefix():
    """`in_session_only=False` = legacy unconditional path."""
    send, prefix = decide_notify(in_session=False, confidence=0, in_session_only=False)
    assert send is True
    assert prefix == ""


def test_disclaimer_text_mentions_off_session_and_killzone():
    """Sanity: the disclaimer doesn't silently become empty."""
    assert "OFF-SESSION" in OFF_SESSION_DISCLAIMER
    assert "killzone" in OFF_SESSION_DISCLAIMER.lower()


# --- settings wiring ---------------------------------------------------------


def test_settings_defaults_match_spec():
    """Default TG_IN_SESSION_ONLY=True and bypass=100 is the bot's
    out-of-the-box behaviour — heartbeats only during session, only
    confidence=100 setups breach the gate off-session."""
    from ictbot.settings import TG_IN_SESSION_ONLY, TG_MIN_CONFIDENCE_BYPASS

    assert TG_IN_SESSION_ONLY is True
    assert TG_MIN_CONFIDENCE_BYPASS == 100
