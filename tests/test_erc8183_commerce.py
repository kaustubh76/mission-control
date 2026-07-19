"""ERC-8183 agent-commerce: regime-report builder + the provider on_job callback.

Hermetic — a synthetic close matrix (no network), the SDK mocked. Covers the deliverable shape,
the degraded path, the on_job → submit_result contract, and a SECURITY check that the deliverable
(sold to other agents) never carries a key/password/path.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ictbot.agent import commerce, regime_report
from ictbot.strategy.momentum_allocator import CONTEST_TOKENS


def _synthetic_close(n: int = 300) -> pd.DataFrame:
    """Deterministic 4h close matrix over the contest universe with a real per-token trend
    spread (so the momentum ranking is non-degenerate). No RNG (kept import-safe)."""
    cols = list(CONTEST_TOKENS)
    data = {}
    for j, c in enumerate(cols):
        drift = 1.0 + 0.0004 * (j - len(cols) / 2)
        data[c] = 100.0 * np.cumprod(np.full(n, drift))
    return pd.DataFrame(data)


def test_build_report_ok_shape():
    rep = regime_report.build_report(_synthetic_close(), query="regime read please")
    assert rep["status"] == "ok"
    assert rep["schema"] == regime_report.REPORT_SCHEMA
    assert rep["query"] == "regime read please"
    assert set(rep["target_weights"]).issubset(set(CONTEST_TOKENS))
    # ranking is sorted by weight desc and matches the weighted set
    assert rep["momentum_ranking"] == sorted(
        rep["target_weights"], key=rep["target_weights"].get, reverse=True
    )
    assert isinstance(rep["regime_score"], float) and isinstance(rep["deploy_cap"], float)
    # CMC provenance is always present (Pro API at minimum)
    assert "pro_api" in rep["cmc_sources"]


def test_build_report_degraded_on_insufficient_data():
    rep = regime_report.build_report(pd.DataFrame({t: [1.0, 2.0] for t in CONTEST_TOKENS}))
    assert rep["status"] == "degraded"
    assert rep["target_weights"] == {} and rep["momentum_ranking"] == []
    assert rep["schema"] == regime_report.REPORT_SCHEMA  # still well-formed


def test_build_report_never_raises_on_none(monkeypatch):
    # Force the live CMC fetch to fail → degraded, not an exception.
    import ictbot.data.cmc as cmc

    monkeypatch.setattr(cmc, "cmc_4h_close_matrix", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))
    monkeypatch.setattr(cmc, "fear_greed", lambda *a, **k: None)
    rep = regime_report.build_report(None)
    assert rep["status"] == "degraded"


def test_on_job_returns_report_json_and_journals(monkeypatch, tmp_path):
    fixed = {"schema": regime_report.REPORT_SCHEMA, "status": "ok", "regime_score": 0.42,
             "target_weights": {"BNB": 0.3}, "momentum_ranking": ["BNB"]}
    monkeypatch.setattr(regime_report, "build_report", lambda **kw: {**fixed, "query": kw.get("query")})
    monkeypatch.setattr(commerce, "COMMERCE_JOURNAL", tmp_path / "commerce_jobs.jsonl")

    out = commerce.on_job({"job_id": 7, "description": "give me your regime read"})
    parsed = json.loads(out)
    assert parsed["status"] == "ok" and parsed["query"] == "give me your regime read"

    rows = [json.loads(l) for l in (tmp_path / "commerce_jobs.jsonl").read_text().splitlines()]
    assert rows[-1]["event"] == "SUBMIT" and rows[-1]["job_id"] == 7


def test_on_job_works_with_dataclass_like_job(monkeypatch, tmp_path):
    """The SDK may pass a Job object (attrs) rather than a dict — on_job must handle both."""
    monkeypatch.setattr(regime_report, "build_report", lambda **kw: {"status": "ok", "query": kw.get("query")})
    monkeypatch.setattr(commerce, "COMMERCE_JOURNAL", tmp_path / "j.jsonl")

    class _Job:
        job_id = 11
        description = "rank the universe"

    out = json.loads(commerce.on_job(_Job()))
    assert out["query"] == "rank the universe"


def test_deliverable_carries_no_secret(monkeypatch):
    """SECURITY: the sold deliverable must never embed a key/password/private path."""
    rep = regime_report.build_report(_synthetic_close())
    blob = json.dumps(rep).lower()
    for forbidden in ("password", "private_key", "privatekey", "secret", "api_key", "apikey",
                      "/users/", "0x" + "a" * 40):
        assert forbidden not in blob, f"deliverable leaked {forbidden!r}"


def test_build_report_includes_cmc_signals_from_journal(monkeypatch):
    """The sold report ships the agent's LIVE CMC tilt — sector rotation + CMC-native momentum +
    on-chain signals — pulled from the latest journaled tick (public market data only)."""
    from ictbot.api import reads

    row = {"event": "REBALANCE",
           "cmc_rotation": {"sector_hits": ["DOGE"], "trending": ["Memes", "Layer 1"],
                            "mom": {"DOGE": 9.9, "BNB": 6.1}},
           "onchain_signals": {"DOGE": {"flow_ratio": 0.62, "top10_pct": 41.0,
                                        "whale_net_usd": -1200.0,
                                        "secret_field": "must-not-ship"}}}
    monkeypatch.setattr(reads, "read_journal", lambda *a, **k: [row])
    rep = regime_report.build_report(_synthetic_close())
    sig = rep["cmc_signals"]
    assert sig["sector_rotation"] == ["DOGE"]
    assert sig["trending_narratives"] == ["Memes", "Layer 1"]
    assert sig["cmc_momentum"] == {"DOGE": 9.9, "BNB": 6.1}
    assert sig["onchain"]["DOGE"]["flow_ratio"] == 0.62
    assert "secret_field" not in sig["onchain"]["DOGE"]  # whitelist drops non-public fields
    assert "live_signals" in rep["cmc_sources"]
    assert "secret_field" not in json.dumps(rep).lower()  # nothing off-whitelist leaks


def test_build_report_cmc_signals_absent_without_journal(monkeypatch):
    """No rotation levers / empty journal → cmc_signals is None (degrades, never errors)."""
    from ictbot.api import reads

    monkeypatch.setattr(reads, "read_journal", lambda *a, **k: [])
    rep = regime_report.build_report(_synthetic_close())
    assert rep["cmc_signals"] is None and rep["status"] == "ok"


def test_available_off_without_flag(monkeypatch):
    monkeypatch.setattr(commerce.settings, "erc8183_enabled", False)
    assert commerce.available() is False


def test_pillars_commerce_surfaces_service_and_preview(monkeypatch):
    """The commerce block advertises the REAL service + a live deliverable preview from the latest
    tick — visible before any on-chain job settles, so the dashboard panel is never blank. Hermetic:
    network + journal stubbed."""
    from ictbot.api import reads

    row = {"event": "REBALANCE", "regime_score": 0.42, "deploy_cap": 0.51,
           "strategy": "momentum_cmc", "rationale": "F&G 55 neutral; tilt to momentum leaders",
           "target": {"BNB": 0.3, "CAKE": 0.2, "DOGE": 0.1}, "ts": "2026-06-16T00:00:00Z"}
    monkeypatch.setattr(reads, "read_journal", lambda *a, **k: [row])
    monkeypatch.setattr(reads, "_pillars_net", lambda: {
        "pay_wallet": "0xEb7bF36aab4912c955474206EF0b835170389655",
        "link": {"registry": "0x8004A169571e2F1Df59Df8b8d3fA7f3e7Bee0000"}})
    monkeypatch.setattr(reads, "_fear_greed_with_fallback", lambda latest: (55, False))

    block = reads.pillars_card()["commerce"]
    svc = block["service"]
    assert svc["name"] == "Market Regime Report"
    assert "erc8183" in svc["capabilities"] and "regime-report" in svc["capabilities"]
    assert svc["agent_id"] == int(reads.settings.agent_id or 0)
    assert svc["provider"].startswith("0x")

    pv = block["preview"]
    assert pv["regime_score"] == 0.42 and pv["deploy_cap"] == 0.51
    assert pv["fear_greed"] == 55
    assert pv["momentum_ranking"] == ["BNB", "CAKE", "DOGE"]  # top-by-weight, desc
    assert pv["strategy"] == "momentum_cmc"

    # SECURITY: the surfaced commerce block must carry no key/password/private path.
    blob = json.dumps(block).lower()
    for forbidden in ("password", "private_key", "privatekey", "secret", "api_key", "/users/"):
        assert forbidden not in blob, f"commerce block leaked {forbidden!r}"


def test_pillars_commerce_preview_none_without_tick(monkeypatch):
    """No rebalance yet → preview is None but the service offering still renders (graceful)."""
    from ictbot.api import reads

    monkeypatch.setattr(reads, "read_journal", lambda *a, **k: [])
    monkeypatch.setattr(reads, "_pillars_net", lambda: {"pay_wallet": None, "link": {}})
    block = reads.pillars_card()["commerce"]
    assert block["preview"] is None
    assert block["service"]["name"] == "Market Regime Report"
