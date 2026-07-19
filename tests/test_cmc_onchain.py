"""
Unit tests for the CMC on-chain (DEX) channel support — pure frame parsers + the store readers.

Fixtures are REAL captured frame shapes from the live `onchain@*` channels (CAKE/LINK on
BSC/Ethereum), so the tests pin the parsers against ground truth. Hermetic: the store readers
are pointed at a temp snapshot file; no network.
"""

from __future__ import annotations

import json
import time

from ictbot.data import cmc_onchain, cmc_stream_store

# --- real captured frames -------------------------------------------------- #
_TOKEN_METRIC = {
    "pid": 14,
    "a": "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",
    "p": 1.4150399680598924,
    "sts": [
        {"win": "4h", "vn": 607443.6, "vu": 864545.6, "pc": 0.3, "txs": 2107, "bc": 912,
         "sc": 1195, "bvu": 464549.1, "svu": 399996.4, "bvn": 326148.2, "svn": 281295.4,
         "ut": 720, "but": 423, "sut": 409, "h": 1.4409, "l": 1.4011},
        {"win": "24h", "vu": 9521162.7, "pc": 7.84, "txs": 2604, "bc": 1296, "sc": 1308,
         "bvu": 3015218.5, "svu": 6505944.2, "ut": 607},
    ],
}
_HOLDERS = {
    "pid": 14, "a": "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82", "tp": "top_share",
    "ts": 1781550268455, "t10p": 3.9934, "t50p": 5.3503, "t100p": 5.6799, "t100ab": 2589719.48,
}
_LIQUIDITY = {
    "pid": 14, "f": "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865", "ts": 1781550270000,
    "tp": "remove", "t0s": "Cake", "t1s": "USDT", "a0": 0.0, "a1": 0.0, "vu": 12500.0,
    "ma": "0x77373f362d6a72192255823d2997a197c8c082c1",
    "tx": "0xe655759f9a9d7442578bcb8550a985bc627efd714590fe8783d90fac9bdf1308",
    "t0a": "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82", "t1a": "0x55d398326f99059ff775485246999027b3197955",
}


# --- classify -------------------------------------------------------------- #
def test_classify_frame():
    assert cmc_onchain.classify_frame({"cid": 1839, "p": 1.0}) == "cex"
    assert cmc_onchain.classify_frame(_TOKEN_METRIC) == "token_metric"
    assert cmc_onchain.classify_frame(_HOLDERS) == "holders"
    assert cmc_onchain.classify_frame(_LIQUIDITY) == "liquidity"
    assert cmc_onchain.classify_frame({"foo": 1}) is None
    assert cmc_onchain.classify_frame("not a dict") is None


# --- parsers --------------------------------------------------------------- #
def test_parse_token_metric():
    p = cmc_onchain.parse_token_metric(_TOKEN_METRIC)
    assert p["price"] == 1.4150399680598924
    w = p["windows"]
    assert set(w) == {"4h", "24h"}
    assert w["24h"]["buy_vol_usd"] == 3015218.5 and w["24h"]["sell_vol_usd"] == 6505944.2
    assert w["24h"]["unique_traders"] == 607.0 and w["24h"]["buys"] == 1296.0
    assert w["4h"]["high"] == 1.4409 and w["4h"]["low"] == 1.4011
    # missing keys are omitted, not zero-filled
    assert "high" not in w["24h"]


def test_parse_token_metric_rejects_bad_shape():
    assert cmc_onchain.parse_token_metric({"a": "0x..", "p": 1.0}) is None  # no sts
    assert cmc_onchain.parse_token_metric({"sts": [{"vu": 1}]}) is None  # window has no `win`


def test_parse_holders():
    h = cmc_onchain.parse_holders(_HOLDERS)
    assert h["top10_pct"] == 3.9934 and h["top100_pct"] == 5.6799
    assert h["top100_balance"] == 2589719.48
    assert cmc_onchain.parse_holders({"a": "0x.."}) is None  # no concentration fields


def test_parse_liquidity_event():
    e = cmc_onchain.parse_liquidity_event(_LIQUIDITY)
    assert e["type"] == "remove" and e["value_usd"] == 12500.0
    assert e["pair"] == "Cake/USDT" and e["tx"].startswith("0xe655")
    assert cmc_onchain.parse_liquidity_event({"tp": "swap", "tx": "0x.."}) is None  # not a liq event


# real captured frames for the new channels
_TOKEN_AGG = {"pid": 14, "a": "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82", "ap": 1.4114, "ts": 1781554539000, "lu": 12141216.56}
_TRANSACTION = {"pid": 14, "tp": "sell", "vu": 25000.0, "t0a": "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",
                "t1a": "0x55d3", "tx": "0x8ecc", "pa": "0x6811"}
def test_classify_new_channels():
    assert cmc_onchain.classify_frame(_TOKEN_AGG) == "token_agg"
    assert cmc_onchain.classify_frame(_TRANSACTION) == "transaction"


def test_parse_token_agg():
    a = cmc_onchain.parse_token_agg(_TOKEN_AGG)
    assert a["liquidity_usd"] == 12141216.56 and a["price"] == 1.4114
    assert cmc_onchain.parse_token_agg({"a": "0x..", "ap": 1.0}) is None  # no lu


def test_parse_transaction():
    t = cmc_onchain.parse_transaction(_TRANSACTION)
    assert t["type"] == "sell" and t["value_usd"] == 25000.0
    assert cmc_onchain.parse_transaction({"tp": "add", "vu": 1, "tx": "0x"}) is None  # not a swap


# --- address map ----------------------------------------------------------- #
def test_onchain_tokens_bnbchain():
    toks = cmc_onchain.onchain_tokens()
    assert set(toks) == {"BNB", "ETH", "CAKE", "LINK", "UNI", "AVAX", "DOT", "DOGE"}
    assert all(t["platform_id"] == 14 for t in toks.values())  # all BNB Smart Chain (BEP-20)
    assert toks["CAKE"]["address"] == "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
    assert toks["BNB"]["address"] == cmc_onchain.WBNB  # native BNB → WBNB proxy for on-chain ops


def test_onchain_tokens_ignores_non_bsc_cache(monkeypatch, tmp_path):
    """The universe is BNB-chain-only: a stale/wrong cache (LINK/UNI on their Ethereum
    `platform_id=1` contracts) must NEVER poison the address map — those tokens self-heal to the
    verified built-in BEP-20. A valid BSC override is still honored."""
    poisoned = tmp_path / "addrs.json"
    poisoned.write_text(json.dumps({"tokens": {
        "LINK": {"platform_id": 1, "address": "0x514910771af9ca656af840dff83e8264ecf986ca"},  # ETH
        "UNI": {"platform_id": 1, "address": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"},   # ETH
        "CAKE": {"platform_id": 14, "address": "0xdeadbeef"},  # valid BSC override → honored
    }}))
    monkeypatch.setattr(cmc_onchain, "_ADDR_CACHE", poisoned)
    toks = cmc_onchain.onchain_tokens()
    assert all(t["platform_id"] == 14 for t in toks.values())  # no Ethereum leak
    assert toks["LINK"] == cmc_onchain.ONCHAIN_TOKENS["LINK"]   # fell back to built-in BEP-20
    assert toks["UNI"] == cmc_onchain.ONCHAIN_TOKENS["UNI"]
    assert toks["CAKE"]["address"] == "0xdeadbeef"             # valid BSC override applied


# --- store readers (hermetic, temp files) ---------------------------------- #
def test_onchain_net_liquidity_usd(monkeypatch, tmp_path):
    now = int(time.time() * 1000)
    snap = {
        "updated_ms": now,
        "tokens": {
            "CAKE": {"ts": now, "events": [
                {"ts": now, "type": "add", "value_usd": 50000.0},
                {"ts": now, "type": "remove", "value_usd": 12500.0},
                {"ts": now - 7200_000, "type": "add", "value_usd": 999999.0},  # >1h old → excluded
            ]},
        },
    }
    p = tmp_path / "onchain_liquidity.json"
    p.write_text(json.dumps(snap))
    monkeypatch.setattr(cmc_stream_store, "_ONCHAIN_LIQ_PATH", p)
    net = cmc_stream_store.onchain_net_liquidity_usd("CAKE", window_s=3600, max_age_s=99999)
    assert net == 50000.0 - 12500.0  # add − remove, old event excluded by the window
    assert cmc_stream_store.onchain_net_liquidity_usd("LINK", max_age_s=99999) is None  # no data


def test_onchain_reader_staleness(monkeypatch, tmp_path):
    stale = int(time.time() * 1000) - 5000_000  # ~83 min old
    p = tmp_path / "onchain_token_metric.json"
    p.write_text(json.dumps({"updated_ms": stale, "tokens": {"CAKE": {"ts": stale, "price": 1.0}}}))
    monkeypatch.setattr(cmc_stream_store, "_ONCHAIN_METRIC_PATH", p)
    assert cmc_stream_store.onchain_token_metrics(max_age_s=3600) == {}  # stale → dropped
    assert "CAKE" in cmc_stream_store.onchain_token_metrics(max_age_s=6000)  # within window → kept
