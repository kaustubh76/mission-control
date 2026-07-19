"""Per-strategy PnL + win-rate scoreboard (ictbot.runtime.performance): the WINDOW win-rate
passthrough (backtest), and the DAY win-rate / net PnL port of web/src/lib/pnl.ts (forward). All
offline — pure functions on synthetic curves; expected values are computed by hand from the
documented pnl.ts algorithm so the Python port can't silently drift from the dashboard."""

from __future__ import annotations

from ictbot.runtime import performance as perf

# A 3-day curve with an intraday point on day 1 (proves EOD-per-day collapse) and a flat day 3.
#   eod: 06-10 -> 1010 (last of the two that day), 06-11 -> 1005, 06-12 -> 1005
#   prev seeds from curve[0] = 1000:
#     06-10: 1010-1000 = +10  (win)
#     06-11: 1005-1010 =  -5  (loss)
#     06-12: 1005-1005 =   0  (flat -> excluded)
#   => wins=1, decided=2, win_rate=0.5 ; net = 1005-1000 = +5 (+0.5%)
_CURVE = [
    ("2026-06-10T00:00:00+00:00", 1000.0),
    ("2026-06-10T12:00:00+00:00", 1010.0),
    ("2026-06-11T00:00:00+00:00", 1005.0),
    ("2026-06-12T00:00:00+00:00", 1005.0),
]


def test_backtest_perf_surfaces_window_winrate_and_total_return():
    stats = {
        "total_return": 0.1234,
        "pct_up": 0.61,
        "mean_ret": 0.004,
        "median_ret": 0.003,
        "worst_week_dd": 0.18,
        "trades_per_week": 11.0,
    }  # extra keys ignored
    out = perf.backtest_perf(stats)
    assert out == {"total_return": 0.1234, "win_rate": 0.61, "mean_ret": 0.004, "median_ret": 0.003}
    # win_rate is the WINDOW win-rate (pct_up), NOT trade/day win-rate
    assert out["win_rate"] == stats["pct_up"]


def test_daily_pnl_collapses_to_eod_and_seeds_from_first_point():
    daily = perf.daily_pnl(_CURVE)
    assert [d["date"] for d in daily] == ["2026-06-10", "2026-06-11", "2026-06-12"]
    assert [round(d["pnl"], 6) for d in daily] == [10.0, -5.0, 0.0]
    assert round(daily[0]["pct"], 6) == 0.01  # 1010/1000 - 1


def test_daily_pnl_empty():
    assert perf.daily_pnl([]) == []


def test_win_rate_excludes_flat_days():
    wins, decided, wr = perf.win_rate(perf.daily_pnl(_CURVE))
    assert (wins, decided, wr) == (1, 2, 0.5)


def test_forward_perf_evaluated():
    out = perf.forward_perf(_CURVE)
    assert out["status"] == "evaluated"
    assert out["net_pnl"] == 5.0 and out["net_pct"] == 0.005
    assert out["win_rate"] == 0.5 and out["wins"] == 1 and out["decided"] == 2
    assert out["n_days"] == 3


def test_forward_perf_flat_track_is_accruing():
    # every tick at the same NAV (the current real state: deploy_cap 0 -> book sits in cash)
    flat = [(f"2026-06-1{d}T00:00:00+00:00", 1000.0) for d in range(4)]
    out = perf.forward_perf(flat)
    assert out["status"] == "accruing"
    assert out["win_rate"] is None and out["decided"] == 0
    assert out["net_pnl"] == 0.0


def test_forward_perf_single_point_and_empty():
    one = perf.forward_perf([("2026-06-10T00:00:00+00:00", 1000.0)])
    assert one["status"] == "accruing" and one["decided"] == 0 and one["n_days"] == 1
    assert perf.forward_perf([])["status"] == "none"
