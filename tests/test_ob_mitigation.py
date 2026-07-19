"""
E3 (ROADMAP §E3) — Order Block mitigation gate.

When `mitigation_bars` is set and the OB has been tapped within that
window, get_ob_poi falls through to the swing-low/high fallback.

Instead of recreating a swing structure that find_swings can detect
(brittle), these tests mock `find_order_block` and verify the
mitigation branch in get_ob_poi.
"""

import pandas as pd

from ictbot.indicators import poi_order_block as pob


def _flat_df(n: int = 30):
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
        }
    )


def _df_that_taps(top: float, n: int = 30):
    """30 bars whose lows = top - 1 → low <= top → OB is tagged on every bar."""
    return pd.DataFrame(
        {
            "open": [top + 0.5] * n,
            "high": [top + 1.0] * n,
            "low": [top - 1.0] * n,  # below top → tapped
            "close": [top + 0.2] * n,
        }
    )


def test_no_ob_falls_back_to_swing_low(monkeypatch):
    """No OB detected → fallback to tail(20) min low."""
    df = _flat_df()
    monkeypatch.setattr(pob, "find_order_block", lambda df, bias, swing_lookback=3: None)
    poi = pob.get_ob_poi(df, "BULLISH", mitigation_bars=10)
    assert poi == 99.0  # tail(20).min()


def test_unmitigated_ob_returns_top_for_demand(monkeypatch):
    """OB exists, not tapped (all lows above top) → return its top."""
    # lows=99 in _flat_df; set OB top=50 so low > top always (never tapped).
    df = _flat_df()
    monkeypatch.setattr(
        pob,
        "find_order_block",
        lambda df, bias, swing_lookback=3: {
            "kind": "DEMAND",
            "top": 50.0,
            "bottom": 45.0,
            "index": 5,
        },
    )
    poi = pob.get_ob_poi(df, "BULLISH", mitigation_bars=10)
    assert poi == 50.0


def test_mitigated_demand_ob_falls_back_to_swing_low(monkeypatch):
    """OB exists AND has been tapped > mitigation_bars ago → fallback."""
    df = _df_that_taps(top=200.0)  # every bar's low (199) <= top (200)
    monkeypatch.setattr(
        pob,
        "find_order_block",
        lambda df, bias, swing_lookback=3: {
            "kind": "DEMAND",
            "top": 200.0,
            "bottom": 195.0,
            "index": 0,  # tapped from bar 0 onwards
        },
    )
    # 30 bars total, OB at index 0; bars_since_tap = 29 > mitigation_bars=5.
    poi = pob.get_ob_poi(df, "BULLISH", mitigation_bars=5)
    fallback = float(round(df["low"].tail(20).min(), 2))
    assert poi == fallback


def test_recent_tap_keeps_ob_alive(monkeypatch):
    """OB tapped only recently (within mitigation_bars) → still active."""
    # Construct a df where only the LAST bar dips below top; all earlier
    # bars are clear above the OB.
    rows = [(105.0, 106.0, 102.0, 103.0)] * 29 + [(103.0, 104.0, 99.0, 100.0)]
    df = pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        }
    )
    monkeypatch.setattr(
        pob,
        "find_order_block",
        lambda df, bias, swing_lookback=3: {
            "kind": "DEMAND",
            "top": 100.0,
            "bottom": 95.0,
            "index": 10,
        },
    )
    # first_tap_index: first bar with low <= 100 is bar 29; bars_since_tap=0.
    # With mitigation_bars=10, 0 > 10 is False → NOT mitigated → return top.
    poi = pob.get_ob_poi(df, "BULLISH", mitigation_bars=10)
    assert poi == 100.0


def test_mitigation_none_returns_ob_top_unconditionally(monkeypatch):
    """mitigation_bars=None preserves legacy behaviour even if tapped."""
    df = _df_that_taps(top=200.0)
    monkeypatch.setattr(
        pob,
        "find_order_block",
        lambda df, bias, swing_lookback=3: {
            "kind": "DEMAND",
            "top": 200.0,
            "bottom": 195.0,
            "index": 0,
        },
    )
    assert pob.get_ob_poi(df, "BULLISH", mitigation_bars=None) == 200.0


def test_supply_ob_mitigation(monkeypatch):
    """Symmetric check on BEARISH bias / SUPPLY OB."""
    # Bars whose highs (201) are above an OB bottom (200) → tagged.
    df = pd.DataFrame(
        {
            "open": [199.5] * 30,
            "high": [201.0] * 30,
            "low": [199.0] * 30,
            "close": [199.8] * 30,
        }
    )
    monkeypatch.setattr(
        pob,
        "find_order_block",
        lambda df, bias, swing_lookback=3: {
            "kind": "SUPPLY",
            "top": 205.0,
            "bottom": 200.0,
            "index": 0,
        },
    )
    poi = pob.get_ob_poi(df, "BEARISH", mitigation_bars=5)
    # Mitigated → fallback to tail(20) max high = 201.
    assert poi == 201.0
