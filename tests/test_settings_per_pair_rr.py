"""
Tests for Fix 9.A — per-pair SL/TP overrides.

The single global `SL_FRAC` / `TP_FRAC` was the dominant edge leak across
5 pairs with very different volatility regimes. These tests assert the
new `settings.get_sl_frac(pair)` / `get_tp_frac(pair)` helpers:

  - return the per-pair override when set
  - fall back to the global default when unset
  - tolerate unknown pairs (returns global, doesn't raise)
  - derive the token correctly from the ccxt symbol shape
"""

from __future__ import annotations

import pytest

from ictbot.settings import Settings


def _build_settings(**overrides) -> Settings:
    """Construct a fresh Settings with the env-file disabled so test
    overrides aren't shadowed by .env values. Pydantic-settings reads
    .env unless we disable it explicitly."""
    s = Settings.model_construct(**Settings().model_dump())
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestPairTokenExtraction:
    @pytest.mark.parametrize(
        "pair, expected",
        [
            ("BTC/USDT:USDT", "BTC"),
            ("ETH/USDT:USDT", "ETH"),
            ("SOL/USDT:USDT", "SOL"),
            ("XRP/USDT:USDT", "XRP"),
            ("btc/usdt:usdt", "BTC"),
        ],
    )
    def test_extracts_base_asset_uppercased(self, pair, expected):
        assert _build_settings()._pair_token(pair) == expected

    @pytest.mark.parametrize("pair", ["", None, "no-slash-pair"])
    def test_returns_empty_for_malformed(self, pair):
        assert _build_settings()._pair_token(pair) == ""


class TestGetSlFrac:
    def test_falls_back_to_global_when_override_unset(self):
        s = _build_settings(sl_frac=0.007)
        assert s.get_sl_frac("BTC/USDT:USDT") == 0.007

    def test_returns_per_pair_override_when_set(self):
        s = _build_settings(sl_frac=0.005, sl_frac_btc=0.003)
        assert s.get_sl_frac("BTC/USDT:USDT") == 0.003

    def test_unknown_pair_falls_back_to_global(self):
        s = _build_settings(sl_frac=0.005)
        # 6th pair never seen — should inherit global, not raise.
        assert s.get_sl_frac("DOGE/USDT:USDT") == 0.005

    def test_other_pairs_unaffected_by_one_override(self):
        s = _build_settings(sl_frac=0.005, sl_frac_btc=0.003)
        assert s.get_sl_frac("ETH/USDT:USDT") == 0.005
        assert s.get_sl_frac("SOL/USDT:USDT") == 0.005

    @pytest.mark.parametrize(
        "pair, attr",
        [
            ("BTC/USDT:USDT", "sl_frac_btc"),
            ("ETH/USDT:USDT", "sl_frac_eth"),
            ("SOL/USDT:USDT", "sl_frac_sol"),
            ("XRP/USDT:USDT", "sl_frac_xrp"),
        ],
    )
    def test_each_configured_pair_has_override_slot(self, pair, attr):
        s = _build_settings(**{attr: 0.0123})
        assert s.get_sl_frac(pair) == 0.0123


class TestGetTpFrac:
    def test_falls_back_to_global_when_override_unset(self):
        s = _build_settings(tp_frac=0.025)
        assert s.get_tp_frac("BTC/USDT:USDT") == 0.025

    def test_returns_per_pair_override_when_set(self):
        s = _build_settings(tp_frac=0.025, tp_frac_sol=0.015)
        assert s.get_tp_frac("SOL/USDT:USDT") == 0.015

    def test_unknown_pair_falls_back_to_global(self):
        s = _build_settings(tp_frac=0.025)
        assert s.get_tp_frac("DOGE/USDT:USDT") == 0.025

    @pytest.mark.parametrize(
        "pair, attr",
        [
            ("BTC/USDT:USDT", "tp_frac_btc"),
            ("ETH/USDT:USDT", "tp_frac_eth"),
            ("SOL/USDT:USDT", "tp_frac_sol"),
            ("XRP/USDT:USDT", "tp_frac_xrp"),
        ],
    )
    def test_each_configured_pair_has_override_slot(self, pair, attr):
        s = _build_settings(**{attr: 0.0456})
        assert s.get_tp_frac(pair) == 0.0456


class TestIndependentFromGlobal:
    """Setting an SL override must not bleed into TP and vice versa."""

    def test_sl_override_doesnt_affect_tp(self):
        s = _build_settings(sl_frac=0.005, tp_frac=0.025, sl_frac_xrp=0.002)
        assert s.get_sl_frac("XRP/USDT:USDT") == 0.002
        assert s.get_tp_frac("XRP/USDT:USDT") == 0.025

    def test_tp_override_doesnt_affect_sl(self):
        s = _build_settings(sl_frac=0.005, tp_frac=0.025, tp_frac_xrp=0.05)
        assert s.get_sl_frac("XRP/USDT:USDT") == 0.005
        assert s.get_tp_frac("XRP/USDT:USDT") == 0.05


# ---- Fix 12.A (Phase 12) per-pair POI tolerance ---------------------------


class TestGetPoiTapTolerance:
    def test_falls_back_to_global_when_override_unset(self):
        s = _build_settings(poi_tap_tolerance=0.005)
        assert s.get_poi_tap_tolerance("BTC/USDT:USDT") == 0.005

    def test_returns_per_pair_override_when_set(self):
        s = _build_settings(poi_tap_tolerance=0.005, poi_tap_tolerance_sol=0.01)
        assert s.get_poi_tap_tolerance("SOL/USDT:USDT") == 0.01

    def test_unknown_pair_falls_back_to_global(self):
        s = _build_settings(poi_tap_tolerance=0.005)
        # Pair not in the active set inherits the global, no raise.
        assert s.get_poi_tap_tolerance("DOGE/USDT:USDT") == 0.005

    def test_other_pairs_unaffected_by_one_override(self):
        s = _build_settings(poi_tap_tolerance=0.005, poi_tap_tolerance_sol=0.01)
        assert s.get_poi_tap_tolerance("BTC/USDT:USDT") == 0.005
        assert s.get_poi_tap_tolerance("ETH/USDT:USDT") == 0.005
        assert s.get_poi_tap_tolerance("XRP/USDT:USDT") == 0.005

    @pytest.mark.parametrize(
        "pair, attr",
        [
            ("BTC/USDT:USDT", "poi_tap_tolerance_btc"),
            ("ETH/USDT:USDT", "poi_tap_tolerance_eth"),
            ("SOL/USDT:USDT", "poi_tap_tolerance_sol"),
            ("XRP/USDT:USDT", "poi_tap_tolerance_xrp"),
        ],
    )
    def test_each_configured_pair_has_override_slot(self, pair, attr):
        s = _build_settings(**{attr: 0.0042})
        assert s.get_poi_tap_tolerance(pair) == 0.0042

    def test_poi_override_doesnt_affect_sl_or_tp(self):
        s = _build_settings(
            sl_frac=0.005,
            tp_frac=0.025,
            poi_tap_tolerance=0.005,
            poi_tap_tolerance_xrp=0.003,
        )
        assert s.get_poi_tap_tolerance("XRP/USDT:USDT") == 0.003
        assert s.get_sl_frac("XRP/USDT:USDT") == 0.005
        assert s.get_tp_frac("XRP/USDT:USDT") == 0.025
