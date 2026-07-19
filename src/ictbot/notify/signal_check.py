"""
One-shot Telegram status ping: "is the strategy producing a signal or not,
and what canonical ICT rules are still missing from the executor?"

Run it with:
    python -m ictbot.notify.signal_check
    python -m ictbot.notify.signal_check --pair BTC/USDT:USDT

It evaluates every configured pair once (no dedup, no journal write) and
sends ONE Telegram message containing:
  1. per-pair signal status (BUY / SELL / NO ENTRY) with blockers
  2. canonical robustness checklist — what to work on next
     (the WRONG / GAP / PARTIAL rows from the legacy ictbot robustness checklist)
"""

from __future__ import annotations

import argparse
import datetime as _dt
from dataclasses import dataclass

from ictbot.notify.telegram import send_telegram
from ictbot.orchestrator.analyzer import analyze_pair
from ictbot.settings import PAIRS

# -----------------------------------------------------------------------------
# Canonical robustness checklist (legacy ictbot robustness checklist,
# section 4b — keep these in sync).
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    status: str  # PASS | PARTIAL | GAP | WRONG
    rule: str
    todo: str


CANONICAL_CHECKS: tuple[Check, ...] = (
    # Items still open
    Check(
        "GAP",
        "Premium/Discount fib filter missing",
        "longs only when price < OB midpoint; shorts > midpoint",
    ),
    Check(
        "PARTIAL",
        "FVG should pick NEAREST unfilled gap",
        "rank candidate FVGs by |price - midpoint|, take min",
    ),
    # Pre-canonical-flow PASS items
    Check("PASS", "HTF (4h) decides bias only", ""),
    Check("PASS", "RR floor 1:2 enforced via grid 'rr2plus'", ""),
    Check("PASS", "Killzone gate (London / NY)", ""),
    Check("PASS", "Delta agrees with HTF bias", ""),
    Check("PASS", "All 4 confidence bits required to fire", ""),
    # Canonical-flow shipped — boxes 1..8
    Check("PASS", "Box 1: strategy_mode=follow default (Phase A)", ""),
    Check("PASS", "Box 1: bias_engine=swing default (Phase A)", ""),
    Check("PASS", "Box 2: POI on htf_df with 3m fallback (Phase F)", ""),
    Check("PASS", "Box 2: poi_engine=order_block default (Phase A)", ""),
    Check("PASS", "Box 3: MSS on 3m POI frame (Phase B)", ""),
    Check("PASS", "Box 4: MFVG must form after MSS bar (Phase C)", ""),
    Check("PASS", "Box 5: MFVG retest (close-inside-range) gate (Phase D)", ""),
    Check("PASS", "Box 7: SL anchored to MFVG edge (sl_anchor=structural)", ""),
    Check("PASS", "Box 8: TP1 = 1:N RR off real R, TP2 = next liquidity", ""),
    Check("PASS", "Structural anchoring works in fade mode (Phase E)", ""),
    Check("PASS", "CANONICAL_FLOW=off rolls back every default at once", ""),
)


# -----------------------------------------------------------------------------
# Per-pair status formatting
# -----------------------------------------------------------------------------

SEP = "━" * 24
TITLE = "🤖 ICTBOT  ·  SIGNAL CHECK"

BIAS_GLYPH = {"BULLISH": "🔺", "BEARISH": "🔻", "NEUTRAL": "▫️"}
STATUS_GLYPH = {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "⚪", "ERROR": "⚠️"}


def _short_pair(pair: str) -> str:
    """'BTC/USDT:USDT' → 'BTC/USDT'."""
    return pair.split(":", 1)[0]


def _fmt_price(price) -> str:
    """Price → string with sane decimals + thousand separators."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "—"
    if p == 0:
        return "—"
    a = abs(p)
    if a >= 1000:
        return f"{p:,.1f}"
    if a >= 10:
        return f"{p:,.2f}"
    if a >= 1:
        return f"{p:,.3f}"
    return f"{p:.5f}"


def _fmt_pct(value, entry) -> str:
    """Signed percentage of `value` relative to `entry`."""
    try:
        pct = (float(value) - float(entry)) / float(entry) * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return "—"
    sign = "+" if pct >= 0 else "−"  # unicode minus for visual alignment
    return f"{sign}{abs(pct):.2f} %"


def _bias_word(b) -> str:
    if not b:
        return "NEUTRAL"
    u = str(b).upper()
    if "BULL" in u:
        return "BULLISH"
    if "BEAR" in u:
        return "BEARISH"
    return "NEUTRAL"


def _trade_levels(result: dict):
    """Return trade levels for the TG card.

    Always returns a 7-tuple (price, sl, tp1, tp2, tp3, rr, is_projected):
        is_projected=False → real bracket the bot WILL place this bar
                             (entry in BUY/SELL).
        is_projected=True  → projected bracket showing what the bot WOULD
                             place if it fired now in the closest direction
                             (entry == NO ENTRY).
        sl/tp1=None        → no projection possible (price missing or
                             strategy returned no proposal).

    Returns None when price itself isn't known.

    The strategy populates `proposed_sl` / `proposed_tp` on every result
    (mirrors the live SL/TP formula in the closest direction) so the card
    can always show a bracket — never the bare "no setup" line that left
    the reader guessing.
    """
    try:
        price = float(result.get("price") or 0)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None

    entry = result.get("entry")
    try:
        sl_raw = float(result.get("sl") or 0)
        tp_raw = float(result.get("tp") or 0)
    except (TypeError, ValueError):
        sl_raw = tp_raw = 0.0

    is_projected = entry not in ("BUY", "SELL")
    if is_projected:
        # No live signal — pull the projection the strategy attached.
        try:
            sl_raw = float(result.get("proposed_sl") or 0)
            tp_raw = float(result.get("proposed_tp") or 0)
        except (TypeError, ValueError):
            sl_raw = tp_raw = 0.0
        direction = result.get("proposed_direction") or "BUY"
    else:
        direction = entry

    if sl_raw <= 0 or tp_raw <= 0 or sl_raw == price:
        # No bracket available even after fallback. Show price only.
        return price, None, None, None, None, None, is_projected

    sign = 1 if direction == "BUY" else -1
    r = abs(price - sl_raw)
    tp1 = tp_raw
    tp2 = price + 2 * r * sign
    tp3 = price + 3 * r * sign
    rr = abs(tp1 - price) / r if r > 0 else 0.0
    return price, sl_raw, tp1, tp2, tp3, rr, is_projected


def _final_status(result: dict) -> str:
    """Status for the pair header."""
    entry = result.get("entry")
    if entry == "BUY":
        return "BUY"
    if entry == "SELL":
        return "SELL"
    return "NEUTRAL"


def _news_line(result: dict) -> str | None:
    """One-line news context for the pair card, or None if no info."""
    ev = result.get("news_event")
    if ev:
        eta = ev.get("eta_minutes")
        eta_str = f"{eta:+.0f} min" if eta is not None else "—"
        return (
            f"   NEWS       ⚠  {ev.get('country', '?')} "
            f"{ev.get('title', '?')}  ({eta_str}, {ev.get('impact', '?')})"
        )
    # Best-effort upcoming-event lookup. `next_event_eta` defaults to the
    # process cache (refreshed at most once per 5 min) so 5 pairs in a row
    # don't each trigger disk + parse work.
    try:
        from ictbot.runtime import news as _news

        next_hit = _news.next_event_eta(
            country=("USD",),
            impact=("High",),
        )
        if next_hit:
            ev2, dt = next_hit
            mins = dt.total_seconds() / 60.0
            if mins < 24 * 60:  # only show if within 24 h
                return f"   NEWS       🕐  next: {ev2.country} {ev2.title} in {mins:+.0f} min"
    except Exception:
        pass
    return None


def _pair_block(result: dict) -> str:
    """Format a single pair into a card. Never raises.

    STRICT real-data policy:
      - ENTRY is the live exchange price (always shown).
      - SL / TP / RR are shown ONLY when the strategy actually decided a
        trade. Otherwise we display 'no setup — sitting out'. Never invent.
      - Bias-row labels use the ACTUAL configured timeframes (HTF/BIAS),
        not cosmetic hard-coded '4H'/'1H' strings.
    """
    try:
        pair = _short_pair(result.get("pair") or "?")
        err = result.get("error")
        if err:
            return f"{STATUS_GLYPH['ERROR']} {pair}  ·  DATA INCOMPLETE\n   {err}"

        levels = _trade_levels(result)
        if levels is None:
            return (
                f"{STATUS_GLYPH['ERROR']} {pair}  ·  NO PRICE DATA\n"
                f"   strategy returned no usable price"
            )
        entry_px, sl, tp1, tp2, tp3, rr, is_projected = levels

        status = _final_status(result)
        htf = _bias_word(result.get("htf_bias"))
        ltf = _bias_word(result.get("ltf_bias"))

        # Real timeframe labels from settings (no cosmetic mislabel).
        from ictbot.settings import BIAS_TIMEFRAME, HTF_TIMEFRAME

        htf_label = f"{HTF_TIMEFRAME.upper()} BIAS".ljust(10)
        ltf_label = f"{BIAS_TIMEFRAME.upper()} BIAS".ljust(10)

        bias_lines = [
            f"   {htf_label} {BIAS_GLYPH[htf]}  {htf}",
            f"   {ltf_label} {BIAS_GLYPH[ltf]}  {ltf}",
            f"   FINAL      {STATUS_GLYPH[status]}  {status}",
        ]
        news = _news_line(result)
        if news:
            bias_lines.append(news)

        lines = [
            f"{STATUS_GLYPH[status]} {pair}  ·  {status}",
            "",
            *bias_lines,
            "",
            f"   ENTRY      {_fmt_price(entry_px)}   (live price)",
        ]

        if sl is None:
            # Even the projection didn't yield a bracket (rare — price
            # missing or strategy returned all-zero proposal).
            lines.append("   bracket unavailable (no price/projection)")
        else:
            rr_str = f"  ·  RR 1:{rr:.2f}" if rr else ""
            if is_projected:
                # Hypothetical bracket — the bot HAS NOT fired this bar.
                # Label every row 'proj' so the reader knows it's a preview.
                direction = result.get("proposed_direction") or "—"
                lines.append(
                    f"   ↳ projected {direction} bracket (bot has not fired — preview only)"
                )
                lines += [
                    f"   SL  proj   {_fmt_price(sl)}   {_fmt_pct(sl, entry_px)}",
                    f"   TP1 proj   {_fmt_price(tp1)}   {_fmt_pct(tp1, entry_px)}"
                    f"   (strategy formula{rr_str})",
                    f"   TP2 proj   {_fmt_price(tp2)}   {_fmt_pct(tp2, entry_px)}"
                    f"   (2R from entry)",
                    f"   TP3 proj   {_fmt_price(tp3)}   {_fmt_pct(tp3, entry_px)}"
                    f"   (3R from entry)",
                ]
            else:
                # Real fire — these are the levels the broker will receive.
                lines += [
                    f"   SL         {_fmt_price(sl)}   {_fmt_pct(sl, entry_px)}",
                    f"   TP1        {_fmt_price(tp1)}   {_fmt_pct(tp1, entry_px)}"
                    f"   (strategy TP{rr_str})",
                    f"   TP2 proj   {_fmt_price(tp2)}   {_fmt_pct(tp2, entry_px)}"
                    f"   (2R from entry)",
                    f"   TP3 proj   {_fmt_price(tp3)}   {_fmt_pct(tp3, entry_px)}"
                    f"   (3R from entry)",
                ]

        # Box 8b of the canonical flow: when the strategy ran in
        # structural mode AND found a next-liquidity level, render it
        # as a separate row so the reader sees the real swing target,
        # not just R-projections. result["tp2"] is 0.0 outside that
        # mode → row is silently skipped, legacy card stays identical.
        try:
            liq = float(result.get("tp2") or 0)
        except (TypeError, ValueError):
            liq = 0.0
        if liq > 0:
            lines.append(
                f"   TP LIQ     {_fmt_price(liq)}   {_fmt_pct(liq, entry_px)}"
                f"   (next external liquidity)"
            )

        return "\n".join(lines)
    except Exception as e:
        # Hardening: never let one bad pair break the whole message.
        pair = (result or {}).get("pair") or "?"
        return f"{STATUS_GLYPH['ERROR']} {pair}  ·  FORMAT ERROR\n   {e}"


def _summary_line(results: list[dict]) -> str:
    """One-line summary footer."""
    fired = [r for r in results if r.get("entry") in ("BUY", "SELL")]
    errs = [r for r in results if r.get("error")]
    neut = [r for r in results if r not in fired and r not in errs]

    def names(rs):
        return ", ".join(_short_pair(r.get("pair") or "?").split("/")[0] for r in rs) or "—"

    return (
        f"📊 {len(results)} pairs  ·  "
        f"🟢 {len(fired)} firing  ·  "
        f"⚪ {len(neut)} neutral  ·  "
        f"⚠️ {len(errs)} errors\n"
        f"   neutral: {names(neut)}\n"
        f"   errors:  {names(errs)}"
    )


def _checks_block() -> str:
    by_status: dict[str, list[Check]] = {}
    for c in CANONICAL_CHECKS:
        by_status.setdefault(c.status, []).append(c)

    parts: list[str] = []

    def section(status: str, header: str, prefix: str) -> None:
        items = by_status.get(status, [])
        if not items:
            return
        parts.append(header)
        for c in items:
            parts.append(f"  {prefix} {c.rule}")
            if c.todo:
                parts.append(f"     → {c.todo}")

    section("WRONG", "⛔ WRONG — blocks live trading", "✗")
    section("GAP", "⚠️ GAP — feature missing", "·")
    section("PARTIAL", "🟡 PARTIAL — incomplete", "·")
    parts.append(
        f"✅ PASS ({len(by_status.get('PASS', []))}): "
        + "  ·  ".join(c.rule for c in by_status.get("PASS", []))
    )
    return "\n".join(parts)


TG_MAX_LEN = 4000  # Telegram caps message body at 4096; leave headroom.


def build_message(results: list[dict], full: bool = False) -> str:
    ts = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d %H:%M UTC")

    header_block = f"{TITLE}\n🕐 {ts}\n{SEP}"
    cards = [_pair_block(r) for r in results]
    pairs_section = f"\n\n{SEP}\n\n".join(cards)
    footer = f"{SEP}\n{_summary_line(results)}"

    msg = f"{header_block}\n\n{pairs_section}\n\n{footer}"

    if full:
        msg += (
            f"\n\n{SEP}\n"
            f"🛠 STRATEGY ROBUSTNESS  ·  what still needs work\n"
            f"   (legacy ictbot robustness checklist §4b)\n\n"
            f"{_checks_block()}"
        )
    else:
        msg += "\n\nℹ run `make signal_check FULL=1` for the robustness checklist."

    # Hard cap for Telegram (4096 char limit). Truncate from the end.
    if len(msg) > TG_MAX_LEN:
        msg = msg[: TG_MAX_LEN - 40] + "\n…\n(truncated — message too long)"
    return msg


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def run_signal_check(pairs: list[str] | None = None, send: bool = True, full: bool = False) -> str:
    pairs = pairs or list(PAIRS)
    # Warm the news cache once so every pair card uses the same snapshot
    # rather than triggering its own (best-effort, never blocking).
    try:
        from ictbot.runtime import news as _news

        _news.refresh_news()
    except Exception:
        pass
    results = [analyze_pair(p, notify=False) for p in pairs]
    msg = build_message(results, full=full)
    if send:
        send_telegram(msg)
    else:
        print(msg)
    return msg


def main() -> None:
    ap = argparse.ArgumentParser(prog="ictbot.notify.signal_check")
    ap.add_argument("--pair", action="append", help="restrict to one pair (repeatable)")
    ap.add_argument(
        "--dry-run", action="store_true", help="print to stdout instead of sending to Telegram"
    )
    ap.add_argument(
        "--full", action="store_true", help="also include the canonical robustness checklist"
    )
    args = ap.parse_args()
    run_signal_check(pairs=args.pair, send=not args.dry_run, full=args.full)


if __name__ == "__main__":
    main()
