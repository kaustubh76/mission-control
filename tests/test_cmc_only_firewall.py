"""The ZERO-CEX firewall (CMC_ONLY). The contest arm must source every price/candle from
CoinMarketCap; any reach into a centralized-exchange path must FAIL LOUD, never silently serve
exchange data. These tests pin that contract:
  1. under cmc_only, cmc.fetch_4h() (Binance/Bybit) RAISES rather than returning CEX candles;
  2. cmc.price() under cmc_only never calls fetch_4h (it uses the CMC quote / CMC 4h stream);
  3. the two-flag boot guard refuses CMC_ONLY without CMC_INTEL_ENABLED (the CMC seed needs it).
Offline — monkeypatch + a subprocess import for the module-level boot guard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ictbot.data import cmc
from ictbot.settings import settings

ROOT = Path(__file__).resolve().parent.parent


def test_fetch_4h_raises_under_cmc_only(monkeypatch):
    monkeypatch.setattr(settings, "cmc_only", True)
    with pytest.raises(RuntimeError, match="CMC_ONLY"):
        cmc.fetch_4h("BNB", limit=300)


def test_price_under_cmc_only_never_touches_fetch_4h(monkeypatch):
    """A live CMC quote returns directly; fetch_4h (the CEX path) is never reached."""
    monkeypatch.setattr(settings, "cmc_only", True)
    monkeypatch.setattr(cmc, "cmc_price", lambda sym, key=None: 612.34)
    calls = {"n": 0}

    def _spy(*a, **k):
        calls["n"] += 1
        raise AssertionError("fetch_4h must NOT be called under cmc_only")

    monkeypatch.setattr(cmc, "fetch_4h", _spy)
    assert cmc.price("BNB") == 612.34
    assert calls["n"] == 0


def test_price_under_cmc_only_falls_back_to_cmc_stream_not_cex(monkeypatch):
    """No live quote -> last close from the CMC 4h stream (fetch_cmc_4h), still no fetch_4h."""
    import pandas as pd

    monkeypatch.setattr(settings, "cmc_only", True)
    monkeypatch.setattr(cmc, "cmc_price", lambda sym, key=None: None)
    monkeypatch.setattr(cmc, "fetch_cmc_4h", lambda sym, limit=250: pd.DataFrame({"close": [9.99]}))

    def _spy(*a, **k):
        raise AssertionError("fetch_4h must NOT be called under cmc_only")

    monkeypatch.setattr(cmc, "fetch_4h", _spy)
    assert cmc.price("LINK") == 9.99


def _import_settings_with_env(**env) -> subprocess.CompletedProcess:
    import os

    e = dict(os.environ)
    e.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        [sys.executable, "-c", "import ictbot.settings"],
        env={**e, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
    )


def test_boot_guard_refuses_cmc_only_without_intel():
    r = _import_settings_with_env(CMC_ONLY="true", CMC_INTEL_ENABLED="false")
    assert r.returncode != 0
    assert "CMC_ONLY=true requires CMC_INTEL_ENABLED=true" in r.stderr


def test_boot_ok_with_both_flags():
    r = _import_settings_with_env(CMC_ONLY="true", CMC_INTEL_ENABLED="true")
    assert r.returncode == 0, r.stderr
