"""
Per-strategy PnL + win-rate — the SCOREBOARD layer (distinct from the survival GATE).

Risk-first survival (worst-week DD < 25% AND >= 7 trades/wk) stays the hard pass/fail rail an
arm must clear to be ranked at all (engine/acceptance.py). This module adds the *performance*
view the operator reads ON TOP of that gate: how much PnL each surviving arm actually posted and
how often it won. It does NOT claim alpha — there is no proven long-only edge on this universe
(docs/bnb_strategy_decision.md §1), so a backtest total-return ranking is dominated by how much an
arm rode the trending sample, not a repeatable edge. Read it as a scoreboard, not a promise.

Two DISTINCT win-rate notions live here — keep them labelled, never conflate:
  - WINDOW win-rate (backtest)  = `pct_up` from portfolio_replay.evaluate — the share of rolling
    7-day (contest-length) windows that finished positive. The robustness/consistency number.
  - DAY win-rate (forward)      = up-days / decided-days on the live EOD NAV series — a byte-for-byte
    Python port of web/src/lib/pnl.ts (`pnlSummary`), so the dashboard % and this reconcile.

PURE: every function operates on plain stats dicts or `(ts_iso, nav)` curves. No journal I/O and no
`scripts` import — the scripts layer (campaign / playbook_status) reads the journals and feeds the
curve in, keeping the dependency direction scripts -> runtime (never the reverse).
"""

from __future__ import annotations

EPS = 1e-9  # matches web/src/lib/pnl.ts — flat days (|pnl| <= EPS) excluded from win-rate


def backtest_perf(stats: dict) -> dict:
    """Surface the PnL + WINDOW win-rate already computed by `portfolio_replay.evaluate`.

    `stats` is the dict `evaluate()` returns (e.g. the campaign's `stats_70`). No new backtest —
    this just names and documents which keys are the performance scoreboard so every caller pulls
    the SAME definition. `win_rate` here is the WINDOW win-rate (`pct_up`); see the module docstring.
    """
    return {
        "total_return": stats.get(
            "total_return"
        ),  # full-period equity return (at binding friction)
        "win_rate": stats.get("pct_up"),  # WINDOW win-rate: share of 7-day windows positive
        "mean_ret": stats.get("mean_ret"),  # mean 7-day-window return
        "median_ret": stats.get("median_ret"),  # median 7-day-window return
    }


def daily_pnl(curve: list[tuple]) -> list[dict]:
    """End-of-day NAV per UTC day -> day-over-day PnL. Port of `dailyPnl` in web/src/lib/pnl.ts.

    `curve` is `[(ts_iso, nav), ...]` in chronological order. The first 10 chars of each ISO
    timestamp are the UTC date (YYYY-MM-DD); the last NAV seen on a date is its EOD NAV. `prev`
    seeds from the FIRST raw NAV point (not the first EOD), exactly as the dashboard does.
    """
    if not curve:
        return []
    eod: dict[str, float] = {}  # date -> last NAV that day (dict preserves chronological insertion)
    for ts, nav in curve:
        eod[ts[:10]] = nav
    out, prev = [], curve[0][1]
    for date, nav in eod.items():
        out.append(
            {
                "date": date,
                "nav": nav,
                "pnl": nav - prev,
                "pct": (nav / prev - 1.0) if prev else 0.0,
            }
        )
        prev = nav
    return out


def win_rate(daily: list[dict]) -> tuple[int, int, float | None]:
    """(wins, decided, win_rate) over a daily series. Port of `winRate` in web/src/lib/pnl.ts.

    wins = up-days (`pnl > EPS`); decided = non-flat days (`|pnl| > EPS`, the denominator); flat
    days (the baseline day, untraded days) are excluded from both. win_rate is None until at least
    one day has resolved.
    """
    wins = sum(1 for d in daily if d["pnl"] > EPS)
    decided = sum(1 for d in daily if abs(d["pnl"]) > EPS)
    return wins, decided, (wins / decided if decided else None)


def forward_perf(curve: list[tuple]) -> dict:
    """Per-arm forward PnL + DAY win-rate from a sorted `(ts_iso, nav)` NAV curve (the isolated
    track). Mirrors `pnlSummary` in web/src/lib/pnl.ts.

    Returns `status="evaluated"` with net_pnl / net_pct / win_rate / wins / decided / n_days once at
    least one day has resolved, else `status="accruing"` (the honest state while the wall-clock
    forward track is still thin — net is still reported, but win_rate stays None). `status="none"`
    when the arm has no NAV points at all.
    """
    if not curve:
        return {"status": "none", "n_days": 0}
    start, current = curve[0][1], curve[-1][1]
    net = current - start
    net_pct = (current / start - 1.0) if start else 0.0
    daily = daily_pnl(curve)
    wins, decided, wr = win_rate(daily)
    return {
        "status": "evaluated" if decided else "accruing",
        "net_pnl": round(net, 4),
        "net_pct": round(net_pct, 4),
        "win_rate": (round(wr, 4) if wr is not None else None),
        "wins": wins,
        "decided": decided,
        "n_days": len(daily),
    }
