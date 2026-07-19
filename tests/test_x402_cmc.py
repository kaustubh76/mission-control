"""Tests for the CMC AI Agent Hub x402 paid-data path (pillar 1, native x402).

NO network or crypto mocks. These offline tests feed a REAL mainnet 402 challenge
(captured verbatim from pro-api.coinmarketcap.com/x402/v1/dex/search) to the REAL
functions, and sign with the REAL bnbagent.X402Signer — EIP-712 signing is offline
and deterministic, so the full build-and-sign path runs for real without spending.
The only thing not exercised here is the on-chain settle (a $0.01 USDC transfer that
needs a funded Base wallet); that lives in tests/test_x402_real_integration.py.
"""

from __future__ import annotations

import base64
import json

import pytest

from ictbot.data import x402_cmc as x

# A REAL 402 challenge captured verbatim from the live mainnet endpoint. The first
# accept is the Base USDC / EIP-3009 option ($0.01, 6dp) the signer pays with; the
# second is a BSC permit2-exact option we deliberately skip. Real data, not a mock.
REAL_CHALLENGE = {
    "x402Version": 2,
    "resource": {"url": "https://pro-api.coinmarketcap.com/x402/v1/dex/search"},
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:8453",
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "payTo": "0x3C5f3a6cE224BB89D72f5EB4232ecC27F67B3eeA",
            "maxTimeoutSeconds": 30,
            "extra": {"name": "USD Coin", "version": "2", "assetTransferMethod": "eip3009"},
            "amount": "10000",
        },
        {
            "scheme": "exact",
            "network": "eip155:56",
            "asset": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
            "payTo": "0x3C5f3a6cE224BB89D72f5EB4232ecC27F67B3eeA",
            "maxTimeoutSeconds": 30,
            "extra": {"name": "USD Coin", "version": "1", "assetTransferMethod": "permit2-exact"},
            "amount": "10000000000000000",
        },
    ],
}
# Throwaway well-known test key (Hardhat account #0) — no funds; proves OFFLINE signing.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def real_signer(usdc: str):
    """A REAL X402Signer over a throwaway wallet, with the same policy _signer() uses."""
    from bnbagent import EVMWalletProvider, X402Signer

    w = EVMWalletProvider(
        password="tpw", private_key=TEST_KEY, persist=False, signing_policy=x._signing_policy(usdc)
    )
    return X402Signer(w, max_value_per_call={usdc: 10000}, session_budget={usdc: 1_000_000})


def test_pick_accept_prefers_base_usdc_eip3009():
    acc = x.pick_accept(REAL_CHALLENGE)
    assert acc["network"] == "eip155:8453"
    assert acc["extra"]["assetTransferMethod"] == "eip3009"
    assert acc["amount"] == "10000"


def test_pick_accept_none_when_nothing_payable():
    assert x.pick_accept({"accepts": []}) is None
    assert x.pick_accept({}) is None


def test_chain_id_parses_caip2():
    assert x._chain_id("eip155:8453") == 8453
    assert x._chain_id("eip155:56") == 56


def test_build_payment_signs_real_eip3009_and_serializes():
    """The REAL signing path: real X402Signer signs the real challenge, the signature
    normalizes to a JSON-safe 0x string, and the PAYMENT-SIGNATURE header round-trips.

    CMC's x402 V2 facilitator requires the chosen `accepted` option AND the `resource`
    echoed in the payload, sent under the PAYMENT-SIGNATURE header (not X-PAYMENT) —
    confirmed live 2026-06-12; this test pins that contract."""
    acc = x.pick_accept(REAL_CHALLENGE)
    payment, receipt = x.build_payment(REAL_CHALLENGE, acc, real_signer(acc["asset"]))
    sig = payment["payload"]["signature"]
    assert isinstance(sig, str) and sig.startswith("0x")  # HexBytes -> 0x string
    # the V2 contract: chosen accept + resource echoed back
    assert payment["accepted"] == acc
    assert payment["resource"] == REAL_CHALLENGE["resource"]
    hdr = x._payment_header(payment)
    assert "X-PAYMENT" not in hdr  # CMC ignores the bare-spec name
    decoded = json.loads(base64.b64decode(hdr["PAYMENT-SIGNATURE"]))
    assert decoded["x402Version"] == 2
    assert decoded["network"] == "eip155:8453"
    auth = decoded["payload"]["authorization"]
    assert auth["value"] == "10000" and auth["to"] == acc["payTo"]
    assert int(auth["validBefore"]) - int(auth["validAfter"]) <= 600  # within policy cap
    assert receipt["value"] == 10000


def test_build_payment_rejects_inflated_value():
    """The per-call cap guard rejects a 402 that asks for more than max_value_per_call."""
    from bnbagent.x402 import X402AmountExceededError

    acc = dict(x.pick_accept(REAL_CHALLENGE))
    acc["amount"] = "99999999"
    with pytest.raises(X402AmountExceededError):
        x.build_payment(REAL_CHALLENGE, acc, real_signer(acc["asset"]))


def test_disabled_returns_none_without_network(monkeypatch):
    """X402_ENABLED off -> no HTTP, no signing, just None (caller uses pro-api)."""
    monkeypatch.setattr(x.settings, "x402_enabled", False, raising=False)
    assert x.fetch_x402(x.DEX_SEARCH_PATH, {"q": "bnb"}) is None
    assert x.dex_search("bnb") is None


def test_enabled_but_unavailable_warns(monkeypatch, caplog):
    """C2: X402_ENABLED=true but unavailable (e.g. no wallet pw / SDK absent) must WARN,
    not silently return None — an operator could otherwise believe pillar-1 is paying."""
    import logging

    monkeypatch.setattr(x.settings, "x402_enabled", True, raising=False)
    monkeypatch.setattr(x.settings, "agent_wallet_password", "", raising=False)
    with caplog.at_level(logging.WARNING):
        assert x.fetch_x402(x.DEX_SEARCH_PATH, {"q": "bnb"}) is None
    assert any("ENABLED but unavailable" in r.message for r in caplog.records)
