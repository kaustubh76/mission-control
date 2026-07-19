"""
Signal journal. Every BUY/SELL the analyzer fires is appended to
signals.json so you can review hit-rate over time without re-running
backtests.

Schema (list of dicts):
  [{
    "ts": "2026-05-26T12:34:56+00:00",
    "pair": "BTC/USDT:USDT",
    "entry": "BUY" | "SELL",
    "price": 77441.3,
    "sl": 77054.1,
    "tp": 78603.0,
    "rr": 3.0,
    "confidence": 100,
    "outcome": "OPEN" | "WIN" | "LOSS",
    "closed_ts": null | ISO timestamp,
    "closed_price": null | float,
    "broker": "paper" | "binance-live" | "delta-live",
    "pnl_r": null | float,
    "entry_fill_price": null | float,
    "fees_paid": null | float
  }, ...]

The `broker` field is the source-of-truth tag for whether
`settle_open_signals` (synthetic bar-OHLC settler) is allowed to close
this row. Paper rows are settled by the synthetic settler; live rows
are settled exclusively by `mark_closed_from_broker` on the broker's
on_close callback path. Defaults to "paper" for backwards-compat with
pre-2026-06 rows that have no broker field.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from ictbot.settings import JOURNAL_FILE


def _read(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def _write(path: Path, entries: list) -> None:
    """J9 (audit gap #17): atomic write so concurrent scanner + dashboard
    can't observe a half-flushed file. Write to a sibling .tmp, then
    `os.replace` (which is atomic on POSIX + Windows). A reader is
    guaranteed to see EITHER the old contents OR the new contents,
    never a partial JSON."""
    import os

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=2, default=str)
    os.replace(tmp, path)


def append_signal(
    pair: str,
    entry: str,
    price: float,
    sl: float,
    tp: float,
    rr: float,
    confidence: int,
    *,
    broker: str = "paper",
    session: str | None = None,
) -> None:
    """Append a new OPEN signal to the journal.

    `broker` is the broker's `.name` attribute (e.g. "paper",
    "binance-live"). Fix 2.A (plan: live P&L clean-up): the synthetic
    settler reads this to decide whether a row may be auto-closed from
    bar high/low — paper yes, live no.

    `session` is the killzone-aware session label at signal-fire time
    (one of "LONDON", "NEW YORK", "TOKYO", "OFF HOURS …" per
    `runtime.sessions.get_sessions()["active_session"]`). Default None
    so existing callers stay compatible; scripts/session_report.py
    falls back to reconstructing from `ts` when the field is missing.
    Fix 16.A (plan: Phase 16 session-bucketed report).
    """
    entries = _read(JOURNAL_FILE)
    entries.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pair": pair,
            "entry": entry,
            "price": price,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "confidence": confidence,
            "outcome": "OPEN",
            "closed_ts": None,
            "closed_price": None,
            "broker": broker,
            "session": session,
        }
    )
    _write(JOURNAL_FILE, entries)


def read_journal(pair: str | None = None, limit: int | None = None) -> list:
    entries = _read(JOURNAL_FILE)
    if pair:
        entries = [e for e in entries if e["pair"] == pair]
    if limit:
        entries = entries[-limit:]
    return entries


def mark_closed_from_broker(order) -> bool:
    """J1 (audit gap #9): the broker is the single source of truth for
    close events. When a broker fires `on_close(order)`, the router
    calls this to update the matching OPEN journal entry with the
    broker's close_price + reason.

    Matches on the most recent OPEN entry for (pair, side). Returns
    True if a journal entry was updated. False means no matching open
    entry — fine for orders placed outside the journal'd flow.
    """
    entries = _read(JOURNAL_FILE)
    target_pair = order.pair
    target_side = order.side
    # Walk newest → oldest so we update the freshest OPEN entry first.
    for e in reversed(entries):
        if e["pair"] != target_pair or e["entry"] != target_side:
            continue
        if e["outcome"] != "OPEN":
            continue
        e["closed_ts"] = (
            order.closed_at.isoformat()
            if order.closed_at is not None
            else datetime.now(timezone.utc).isoformat()
        )
        e["closed_price"] = float(order.close_price) if order.close_price is not None else None
        reason = (order.close_reason or "").upper()
        if reason == "TP":
            e["outcome"] = "WIN"
        elif reason == "SL":
            e["outcome"] = "LOSS"
        elif reason in ("BE", "MANUAL"):
            e["outcome"] = "BE"
        else:
            e["outcome"] = "CLOSED"
        # Fix 2.F (plan: live P&L clean-up): persist the broker-truth
        # P&L numbers so post-hoc analysis doesn't have to re-derive R
        # from prices (which is wrong once fees are present). The
        # OPEN row's `price` field holds the strategy entry; the actual
        # fill (if the broker resolved it via Fix 2.E) is `order.entry`,
        # which we stamp as `entry_fill_price`. Paper / pre-fix orders
        # leave fees_paid=None so realised_pnl_R returns the legacy R.
        try:
            e["pnl_r"] = order.realised_pnl_R()
        except Exception:  # defensive — never block journal write
            e["pnl_r"] = None
        e["entry_fill_price"] = float(order.entry) if order.entry is not None else None
        e["fees_paid"] = float(order.fees_paid) if order.fees_paid is not None else None
        _write(JOURNAL_FILE, entries)
        return True
    return False


def settle_open_signals(current_prices: dict[str, dict]) -> int:
    """For every OPEN signal whose pair has a fresh candle, check if
    high/low has crossed SL or TP and mark WIN/LOSS accordingly.

    `current_prices[pair]` should be a dict with 'high' and 'low' keys
    representing the most recent candle.

    Returns the number of signals settled.
    """
    entries = _read(JOURNAL_FILE)
    settled = 0
    now = datetime.now(timezone.utc).isoformat()

    for e in entries:
        if e["outcome"] != "OPEN":
            continue
        # Defensive: REJECTED rows (cap rejections written by router._journal_rejected)
        # are not real positions. Settling them against bar high/low yields phantom
        # WIN/LOSS outcomes that pollute the journal and any P&L analysis built
        # on top of it. Skip anything whose `entry` doesn't match BUY/SELL exactly.
        if e.get("entry") not in ("BUY", "SELL"):
            continue
        # Fix 2.B (plan: live P&L clean-up): the synthetic settler MUST
        # NOT close real broker rows. Real bracket fills happen with
        # slippage off the trigger price; the live broker's _on_close
        # callback feeds the actual fill (with ccxt's order["average"])
        # back through mark_closed_from_broker. If the synthetic settler
        # closes the row first with bar high/low, the row is committed
        # as `closed_price = e["sl"]` (or e["tp"]) bit-for-bit — which
        # is the signature of the bug Phase 1 found in the journal.
        # Backwards-compat: rows without a broker field (pre-fix) are
        # treated as "paper" so existing tests + historical paper
        # rows still settle.
        if e.get("broker", "paper") != "paper":
            continue
        bar = current_prices.get(e["pair"])
        if not bar:
            continue
        hi, lo = bar["high"], bar["low"]
        if e["entry"] == "BUY":
            if lo <= e["sl"]:
                e["outcome"], e["closed_ts"], e["closed_price"] = "LOSS", now, e["sl"]
                settled += 1
            elif hi >= e["tp"]:
                e["outcome"], e["closed_ts"], e["closed_price"] = "WIN", now, e["tp"]
                settled += 1
        else:  # SELL
            if hi >= e["sl"]:
                e["outcome"], e["closed_ts"], e["closed_price"] = "LOSS", now, e["sl"]
                settled += 1
            elif lo <= e["tp"]:
                e["outcome"], e["closed_ts"], e["closed_price"] = "WIN", now, e["tp"]
                settled += 1

    if settled:
        _write(JOURNAL_FILE, entries)
    return settled


def score_journal(entries: list | None = None) -> dict:
    """Aggregate stats across the journal."""
    if entries is None:
        entries = _read(JOURNAL_FILE)
    wins = sum(1 for e in entries if e["outcome"] == "WIN")
    losses = sum(1 for e in entries if e["outcome"] == "LOSS")
    opens = sum(1 for e in entries if e["outcome"] == "OPEN")
    closed = wins + losses
    return {
        "total": len(entries),
        "wins": wins,
        "losses": losses,
        "open": opens,
        "win_rate": (100.0 * wins / closed) if closed else None,
    }
