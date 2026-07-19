"""
Regression test for the _bars_needed off-by-one that caused 93.8 % of
1m-replay bars to fall into INSUFFICIENT_DATA on a 5000-bar SOL run.

Before the fix: floor division of 5000//240 = 20 → request 70 HTF bars
for a 50-bar minimum + 20 in-window. But the 1m window actually spans
20.83 HTF bars, so at T_start we had 70 − 21 = 49 HTF bars in the
slice, just below MIN_BARS["htf"] = 50. The check failed for the
entire warmup-after-start period (~4 hours of 1m bars).

After the fix: ceil division + 1 buffer → 22 in-window + 50 warmup = 72
total → at T_start we have 72 − 21 = 51 ≥ 50.
"""

from ictbot.engine.backtest import _bars_needed
from ictbot.strategy.ict_pro_max import MIN_BARS


def test_5000_bars_4h_clears_min_at_replay_start():
    htf_need = _bars_needed("4h", 5000, MIN_BARS["htf"])
    in_window = -(-5000 // 240)  # = 21
    at_start = htf_need - in_window
    assert at_start >= MIN_BARS["htf"], (
        f"HTF slice at T_start would be {at_start} bars, need >= {MIN_BARS['htf']}"
    )


def test_exact_multiple_window_still_has_headroom():
    # 4800 1m bars = exactly 20 4h bars. The +1 buffer protects us here.
    htf_need = _bars_needed("4h", 4800, MIN_BARS["htf"])
    in_window = 20
    at_start = htf_need - in_window
    assert at_start >= MIN_BARS["htf"]


def test_15m_3m_also_clear_their_minimums():
    bars = 5000
    bias_need = _bars_needed("15m", bars, MIN_BARS["bias"])
    poi_need = _bars_needed("3m", bars, MIN_BARS["poi"])

    bias_in_window = -(-bars // 15)
    poi_in_window = -(-bars // 3)

    assert bias_need - bias_in_window >= MIN_BARS["bias"]
    assert poi_need - poi_in_window >= MIN_BARS["poi"]
