"""
Phase B — Box 3 of the canonical flow: MSS confirmation on the 3m POI
frame, not the 1m entry frame.

The dispatch knob `mss_timeframe` flips which DataFrame is passed into
`get_ltf_mss`. These tests verify the routing without touching MSS
internals — they assert which frame the strategy sent to the MSS
indicator under each mode.
"""

from __future__ import annotations

import pandas as pd

from ictbot.strategy.ict_pro_max import ICTProMaxStrategy


def _bar(o, h, l, c, v=1.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _ascending(n: int, start: float = 90.0, step: float = 0.5) -> pd.DataFrame:
    rows = [
        _bar(start + i * step, start + i * step + 0.5, start + i * step - 0.5, start + i * step)
        for i in range(n)
    ]
    return pd.DataFrame(rows)


def _session() -> dict:
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


def test_default_mss_timeframe_is_poi():
    """Spec default: MSS runs on 3m POI frame."""
    s = ICTProMaxStrategy()
    assert s.mss_timeframe == "poi"


def test_mss_routes_to_poi_frame_under_default(monkeypatch):
    """When mss_timeframe='poi' (default), strategy passes poi_df to
    get_ltf_mss. We monkey-patch the MSS indicator and capture which
    frame arrived."""
    from ictbot.strategy import ict_pro_max as strat_mod

    captured = {}

    def fake_mss(df, bias, mode="swing"):
        captured["frame_id"] = id(df)
        captured["len"] = len(df)
        return "NO MSS"

    monkeypatch.setattr(strat_mod, "get_ltf_mss", fake_mss)

    htf_df = _ascending(60)
    bias_df = _ascending(30)
    poi_df = _ascending(40)  # distinct length so we can identify it
    entry_df = _ascending(10)  # different length

    s = ICTProMaxStrategy(mss_timeframe="poi")
    s.evaluate(htf_df, bias_df, poi_df, entry_df, _session())

    assert captured["frame_id"] == id(poi_df), "MSS should have received poi_df"
    assert captured["len"] == 40


def test_mss_routes_to_entry_frame_when_legacy_opt_in(monkeypatch):
    """Legacy callers can pass mss_timeframe='entry' to preserve the
    pre-Phase-B behaviour. The entry_df should arrive at MSS."""
    from ictbot.strategy import ict_pro_max as strat_mod

    captured = {}

    def fake_mss(df, bias, mode="swing"):
        captured["frame_id"] = id(df)
        captured["len"] = len(df)
        return "NO MSS"

    monkeypatch.setattr(strat_mod, "get_ltf_mss", fake_mss)

    htf_df = _ascending(60)
    bias_df = _ascending(30)
    poi_df = _ascending(40)
    entry_df = _ascending(10)

    s = ICTProMaxStrategy(mss_timeframe="entry")
    s.evaluate(htf_df, bias_df, poi_df, entry_df, _session())

    assert captured["frame_id"] == id(entry_df), "MSS should have received entry_df"
    assert captured["len"] == 10


def test_kill_switch_reverts_mss_timeframe_to_entry(monkeypatch):
    """CANONICAL_FLOW=off must roll mss_timeframe back to 'entry'
    alongside the other Phase-A flags."""
    import importlib
    import os

    saved = os.environ.get("CANONICAL_FLOW")
    try:
        os.environ["CANONICAL_FLOW"] = "off"
        import ictbot.settings as smod

        importlib.reload(smod)
        assert smod.settings.mss_timeframe == "entry"
    finally:
        if saved is None:
            os.environ.pop("CANONICAL_FLOW", None)
        else:
            os.environ["CANONICAL_FLOW"] = saved
        import ictbot.settings as smod

        importlib.reload(smod)
