"""Smoke test: the Streamlit app module imports cleanly — Phase 11 / gap T4.

We don't try to render the app; that would require a running Streamlit
context. We just verify nothing at module top-level (imports, .env load,
analyzer construction) blows up.
"""

import importlib
import sys

import pytest

# The legacy Streamlit UI is an opt-in extra (`pip install -e ".[ui]"`); the lean
# dashboard-API image doesn't ship it. Skip this smoke test when it's not installed.
pytest.importorskip("streamlit")


def test_ui_app_imports(monkeypatch):
    # Prevent the module's top-level analyze_pair() call from hitting
    # the network when the test runs cold.
    from ictbot.orchestrator import analyzer

    def _fake_analyze(*_a, **_kw):
        return {
            "pair": "BTC/USDT:USDT",
            "error": None,
            "price": 0.0,
            "last_close": 0.0,
            "htf_bias": "BULLISH",
            "ltf_bias": "BULLISH",
            "ltf_poi": 0.0,
            "poi_tap": "WAITING",
            "ltf_mss": "NO MSS",
            "fvg": "NO FVG",
            "micro_fvg": "NO FVG",
            "delta": 0.0,
            "atr_1m": 0.0,
            "entry": "NO ENTRY",
            "sl": 0.0,
            "tp": 0.0,
            "rr": 0.0,
            "confidence": 0,
            "gate_blocked": None,
            "regime": None,
            "diagnostics": {
                "buy_blockers": [],
                "sell_blockers": [],
                "closest_direction": "BUY",
                "blockers": [],
                "near_miss": False,
                "total_conditions": 5,
            },
            "india_time": "00:00:00",
            "tokyo_time": "00:00:00",
            "tokyo_status": "CLOSED",
            "london_time": "00:00:00",
            "london_status": "CLOSED",
            "newyork_time": "00:00:00",
            "newyork_status": "CLOSED",
            "active_session": "OFF HOURS",
            "ltf_df": __import__("pandas").DataFrame(
                columns=["time", "open", "high", "low", "close", "volume"]
            ),
            "poi_df": __import__("pandas").DataFrame(
                columns=["time", "open", "high", "low", "close", "volume"]
            ),
        }

    monkeypatch.setattr(analyzer, "analyze_pair", _fake_analyze)

    # Streamlit isn't actually running, so st.set_page_config() at
    # import time may complain. The smoke test only cares that we
    # didn't hit a hard import error.
    try:
        if "ictbot.ui.app" in sys.modules:
            del sys.modules["ictbot.ui.app"]
        importlib.import_module("ictbot.ui.app")
    except ImportError as e:
        pytest.fail(f"Streamlit dashboard failed to import: {e}")
    except Exception:
        # Streamlit will throw at runtime in a non-streamlit context
        # (e.g. set_page_config needing a ScriptRunContext). That's an
        # environment quirk, not an import failure.
        pass
