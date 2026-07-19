"""
Opt-in REAL CMC integration test — hits the live CMC Startup-tier endpoints.

Skipped by default (no network in CI). Activate with:
    RUN_CMC_INTEGRATION=1 PYTHONPATH=src pytest -q tests/test_cmc_real_integration.py

Verifies what the offline captured-fixture tests cannot: the live endpoints answer,
the CmcClient parses `status.credit_count` and stays under budget, and the intel
fetchers parse the REAL (server-side-drift-prone) response shapes. Costs ~10 credits.
"""

from __future__ import annotations

import os

import pytest

RUN = os.environ.get("RUN_CMC_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(not RUN, reason="set RUN_CMC_INTEGRATION=1 to hit live CMC")


def test_client_records_credits_under_budget():
    from ictbot.data.cmc_client import CmcClient

    c = CmcClient()
    if not c.telemetry()["key_set"]:
        pytest.skip("no CMC_API_KEY configured")
    body = c.get("/v1/global-metrics/quotes/latest", {}, force=True, cache_ttl=0)
    assert body is not None
    assert body["data"]["quote"]["USD"]["total_market_cap"] > 0
    tel = c.telemetry()
    assert tel["last_status"] == 200
    assert tel["last_credit_count"] >= 1  # real credit cost was accounted
    assert tel["credits_today"] <= tel["daily_budget"]
    print(f"\nglobal-metrics cost: {tel['last_credit_count']} credit(s)")


def test_intel_fetchers_parse_live_shapes(monkeypatch):
    from ictbot.data import cmc_intel

    monkeypatch.setattr(cmc_intel.settings, "cmc_intel_enabled", True)
    monkeypatch.setattr(cmc_intel, "_capability", lambda: {})  # attempt all (probe-independent)

    gm = cmc_intel.global_metrics()
    assert gm and gm["btc_dominance"] and gm["total_market_cap"]
    fh = cmc_intel.fng_history(7)
    assert fh and all("value" in r for r in fh)
    ri = cmc_intel.build_regime_intel()
    assert ri and ri["btc_dominance"] is not None and ri["fng_now"] is not None
    df = cmc_intel.daily_ohlcv("BNB", days=10)
    assert df is not None and list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    print(
        f"\nlive intel: btc_dom={gm['btc_dominance']:.1f}% "
        f"mktcap=${gm['total_market_cap'] / 1e12:.2f}T fng_now={ri['fng_now']} ohlcv_rows={len(df)}"
    )
