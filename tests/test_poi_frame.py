"""
Phase F — Box 2 of the canonical flow: POI computed on the HTF frame
with optional fallback to the LTF (3m) frame.

Three modes under test:
  - "htf"          — strict 4h POI only.
  - "htf_then_poi" — try HTF; on WAITING, fall back to 3m. Default.
  - "poi"          — legacy 3m POI only (pre-Phase-F).

We don't probe POI math; we assert which FRAME the strategy passes to
get_ob_poi / get_ltf_poi / get_poi_tap / is_mitigated. The actual
indicator behaviour is exercised by their existing tests.
"""

from __future__ import annotations

import pandas as pd

from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _bar(o, h, l, c, v=1.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _frame(n: int, prefix_id: float) -> pd.DataFrame:
    """A frame with `n` rows. Set a column to a unique value so we can
    identify which frame arrived in monkey-patched callees."""
    rows = [_bar(100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100.5 + i * 0.1) for i in range(n)]
    df = pd.DataFrame(rows)
    df["frame_id"] = prefix_id
    return df


def _session():
    return {
        "killzone_active": True,
        "india_time": "10:00",
        "tokyo_time": "13:30",
        "tokyo_status": "OPEN",
        "london_time": "05:30",
        "london_status": "OPEN",
        "newyork_time": "00:30",
        "newyork_status": "CLOSED",
        "active_session": "LONDON",
    }


def _patch_indicators(monkeypatch, htf_tap: str, poi_tap: str, capture: dict):
    """Stub all four POI-touching indicators. `htf_tap` is what
    get_poi_tap returns when called on the htf frame (frame_id=4.0),
    `poi_tap` is what it returns on the poi frame (frame_id=3.0)."""
    from ictbot.strategy import ict_pro_max as strat_mod

    def fake_ob_poi(df, bias, mitigation_bars=None, tick_size=None):
        capture.setdefault("ob_poi_frames", []).append(float(df["frame_id"].iloc[0]))
        return 100.0

    def fake_ltf_poi(df, bias, tick_size=None):
        capture.setdefault("ltf_poi_frames", []).append(float(df["frame_id"].iloc[0]))
        return 100.0

    def fake_get_poi_tap(df, poi, tolerance_frac=None):
        frame_id = float(df["frame_id"].iloc[0])
        capture.setdefault("tap_frames", []).append(frame_id)
        if frame_id == 4.0:
            return htf_tap
        return poi_tap

    def fake_is_mitigated(df, poi, side="demand", retire_bars=None):
        return False

    monkeypatch.setattr(strat_mod, "get_ob_poi", fake_ob_poi)
    monkeypatch.setattr(strat_mod, "get_ltf_poi", fake_ltf_poi)
    monkeypatch.setattr(strat_mod, "get_poi_tap", fake_get_poi_tap)
    monkeypatch.setattr(strat_mod, "is_mitigated", fake_is_mitigated)


def test_default_poi_frame_is_htf_then_poi():
    s = ICTProMaxStrategy()
    assert s.poi_frame == "htf_then_poi"


def test_strict_htf_only_uses_htf_frame(monkeypatch):
    """poi_frame='htf' must compute POI on htf_df, regardless of result."""
    capture = {}
    _patch_indicators(monkeypatch, htf_tap="WAITING", poi_tap="POI TAPPED", capture=capture)

    s = ICTProMaxStrategy(poi_frame="htf", poi_engine="order_block")
    s.evaluate(_frame(60, 4.0), _frame(30, 1.5), _frame(40, 3.0), _frame(10, 1.0), _session())

    # Only the HTF frame (frame_id=4.0) should appear in ob_poi calls.
    assert capture["ob_poi_frames"] == [4.0]
    # Tap was checked on HTF too — even though it said WAITING, the
    # strict-htf mode does NOT fall back.
    assert capture["tap_frames"] == [4.0]


def test_legacy_poi_only_uses_poi_frame(monkeypatch):
    """poi_frame='poi' = pre-Phase-F behaviour: 3m only."""
    capture = {}
    _patch_indicators(monkeypatch, htf_tap="POI TAPPED", poi_tap="WAITING", capture=capture)

    s = ICTProMaxStrategy(poi_frame="poi", poi_engine="order_block")
    s.evaluate(_frame(60, 4.0), _frame(30, 1.5), _frame(40, 3.0), _frame(10, 1.0), _session())

    # POI frame only (3m → frame_id=3.0).
    assert capture["ob_poi_frames"] == [3.0]
    assert capture["tap_frames"] == [3.0]


def test_htf_then_poi_stops_when_htf_taps(monkeypatch):
    """When HTF gives a tap, the fallback to 3m must NOT run.
    Catches a regression where someone calls both unconditionally."""
    capture = {}
    _patch_indicators(monkeypatch, htf_tap="POI TAPPED", poi_tap="POI TAPPED", capture=capture)

    s = ICTProMaxStrategy(poi_frame="htf_then_poi", poi_engine="order_block")
    s.evaluate(_frame(60, 4.0), _frame(30, 1.5), _frame(40, 3.0), _frame(10, 1.0), _session())

    # Only HTF frame queried — no fallback.
    assert capture["ob_poi_frames"] == [4.0]
    assert capture["tap_frames"] == [4.0]


def test_htf_then_poi_falls_back_when_htf_waiting(monkeypatch):
    """When HTF returns WAITING, fall back to 3m. Both frames queried."""
    capture = {}
    _patch_indicators(monkeypatch, htf_tap="WAITING", poi_tap="POI TAPPED", capture=capture)

    s = ICTProMaxStrategy(poi_frame="htf_then_poi", poi_engine="order_block")
    s.evaluate(_frame(60, 4.0), _frame(30, 1.5), _frame(40, 3.0), _frame(10, 1.0), _session())

    # First HTF (no tap), then 3m (tap).
    assert capture["ob_poi_frames"] == [4.0, 3.0]
    assert capture["tap_frames"] == [4.0, 3.0]


def test_kill_switch_reverts_poi_frame_to_poi(monkeypatch):
    """CANONICAL_FLOW=off must drop poi_frame back to legacy."""
    import importlib
    import os

    saved = os.environ.get("CANONICAL_FLOW")
    try:
        os.environ["CANONICAL_FLOW"] = "off"
        import ictbot.settings as smod

        importlib.reload(smod)
        assert smod.settings.poi_frame == "poi"
    finally:
        if saved is None:
            os.environ.pop("CANONICAL_FLOW", None)
        else:
            os.environ["CANONICAL_FLOW"] = saved
        import ictbot.settings as smod

        importlib.reload(smod)
