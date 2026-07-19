"""Real-data guard tests for notify/signal_check.

History: this file originally enforced "STRICT real-data → never invent
SL/TP". That was changed when the user asked for the TG card to always
show a *projected* bracket so the reader knows what the bot WOULD do.

Current invariants:
  1. When the strategy fires (entry in BUY/SELL) the card MUST show the
     real strategy SL/TP, not a derived projection.
  2. When the strategy does NOT fire (entry=NO ENTRY) the card MUST show
     the strategy's `proposed_sl` / `proposed_tp` and label them as a
     projection — never invented from thin air. If the strategy returned
     no proposal either, the card omits the bracket entirely.
"""

from __future__ import annotations

import pytest

from ictbot.notify import signal_check as S


def _result(
    entry="NO ENTRY",
    sl=0.0,
    tp=0.0,
    *,
    price=67000.0,
    proposed_direction="SELL",
    proposed_sl=0.0,
    proposed_tp=0.0,
    htf_bias="BEARISH",
    ltf_bias="BEARISH",
) -> dict:
    return {
        "pair": "BTC/USDT:USDT",
        "entry": entry,
        "price": price,
        "sl": sl,
        "tp": tp,
        "proposed_direction": proposed_direction,
        "proposed_sl": proposed_sl,
        "proposed_tp": proposed_tp,
        "htf_bias": htf_bias,
        "ltf_bias": ltf_bias,
        "error": None,
        "news_event": None,
    }


def test_no_entry_with_no_proposal_returns_none_for_levels():
    """NO ENTRY + no proposed_sl/tp → SL/TPs/rr are None (card omits)."""
    out = S._trade_levels(_result())
    price, sl, tp1, tp2, tp3, rr, is_projected = out
    assert price == 67000.0
    assert sl is None
    assert tp1 is None
    assert tp2 is None
    assert tp3 is None
    assert rr is None
    # When no levels are available, projected flag still reflects intent.
    assert is_projected is True


def test_no_entry_with_proposal_returns_projected_levels():
    """NO ENTRY but the strategy attached a proposal → card uses it and
    flags is_projected=True so the formatter labels every row as proj."""
    out = S._trade_levels(
        _result(price=100.0, proposed_direction="SELL", proposed_sl=101.0, proposed_tp=98.0)
    )
    price, sl, tp1, tp2, tp3, rr, is_projected = out
    assert price == 100.0
    assert sl == 101.0
    assert tp1 == 98.0
    assert tp2 == pytest.approx(98.0)
    assert tp3 == pytest.approx(97.0)
    assert rr == pytest.approx(2.0)
    assert is_projected is True


def test_buy_returns_real_strategy_values_not_projected():
    out = S._trade_levels(_result(entry="BUY", price=100.0, sl=99.0, tp=102.0))
    price, sl, tp1, tp2, tp3, rr, is_projected = out
    assert price == 100.0
    assert sl == 99.0
    assert tp1 == 102.0
    assert tp2 == pytest.approx(102.0)
    assert tp3 == pytest.approx(103.0)
    assert rr == pytest.approx(2.0)
    assert is_projected is False


def test_sell_returns_mirrored_projections():
    out = S._trade_levels(_result(entry="SELL", price=100.0, sl=101.0, tp=98.0))
    price, sl, tp1, tp2, tp3, rr, is_projected = out
    assert tp1 == 98.0
    assert tp2 == pytest.approx(98.0)
    assert tp3 == pytest.approx(97.0)
    assert rr == pytest.approx(2.0)
    assert is_projected is False


def test_card_shows_projected_bracket_when_no_entry_has_proposal():
    block = S._pair_block(
        _result(price=100.0, proposed_direction="SELL", proposed_sl=101.0, proposed_tp=98.0)
    )
    assert "(live price)" in block
    assert "projected" in block.lower()
    assert "SL  proj" in block
    assert "TP1 proj" in block
    assert "TP2 proj" in block
    assert "TP3 proj" in block


def test_card_omits_bracket_when_no_proposal():
    """NO ENTRY + no proposal → card shows price only, says bracket unavailable."""
    block = S._pair_block(_result(entry="NO ENTRY", sl=0.0, tp=0.0))
    assert "(live price)" in block
    assert "bracket unavailable" in block
    lines = block.splitlines()
    assert not any(l.lstrip().startswith("SL ") for l in lines)
    assert not any(l.lstrip().startswith("TP1") for l in lines)


def test_card_includes_strategy_levels_when_firing():
    block = S._pair_block(
        _result(entry="BUY", price=100.0, sl=99.0, tp=102.0, htf_bias="BULLISH", ltf_bias="BULLISH")
    )
    assert "SL " in block
    assert "TP1" in block
    assert "TP2 proj" in block
    assert "TP3 proj" in block
    assert "RR 1:2.00" in block
    # Real fire — must NOT carry the "projected bracket (bot has not fired)" banner.
    assert "bot has not fired" not in block


def test_bias_labels_use_configured_timeframes(monkeypatch):
    """The card must label biases with the ACTUAL configured timeframes,
    not cosmetic '4H' / '1H' strings."""
    import ictbot.settings as settings

    monkeypatch.setattr(settings, "HTF_TIMEFRAME", "1h")
    monkeypatch.setattr(settings, "BIAS_TIMEFRAME", "5m")
    block = S._pair_block(_result())
    assert "1H BIAS" in block
    assert "5M BIAS" in block
    assert "4H BIAS" not in block
