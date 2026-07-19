"""
Opt-in REAL CMC x402 integration test — hits the live mainnet endpoint.

Skipped by default (no network in CI, no funded wallet). Activate with:
    RUN_X402_INTEGRATION=1 pytest -q tests/test_x402_real_integration.py

What it verifies that offline tests cannot:
  - the live CMC x402 endpoint really answers 402 with a payable Base-USDC
    EIP-3009 accept (the challenge shape can drift server-side);
  - the REAL X402Signer signs that REAL challenge end-to-end (no mocks).

The on-chain SETTLE (a real $0.01 USDC transfer) is a separate opt-in: set
RUN_X402_SETTLE=1 as well AND fund the agent wallet on Base — only then does
the resend actually spend and return data.
"""

from __future__ import annotations

import os

import pytest

from ictbot.data import x402_cmc as x

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_X402_INTEGRATION") != "1",
    reason="set RUN_X402_INTEGRATION=1 to hit the live CMC x402 endpoint",
)

# Throwaway key — signing only, never funded. Real EIP-712 signature, no spend.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _signer(usdc: str):
    from bnbagent import EVMWalletProvider, X402Signer

    w = EVMWalletProvider(
        password="tpw", private_key=TEST_KEY, persist=False, signing_policy=x._signing_policy(usdc)
    )
    return X402Signer(w, max_value_per_call={usdc: 10000}, session_budget={usdc: 1_000_000})


def test_live_challenge_is_payable():
    ch = x.fetch_challenge(x.DEX_SEARCH_PATH, {"q": "bnb"})
    assert ch and ch.get("x402Version") == 2, f"unexpected challenge: {ch}"
    acc = x.pick_accept(ch)
    assert acc is not None, "no payable accept in the live challenge"
    assert acc["network"] == "eip155:8453"
    assert acc["extra"]["assetTransferMethod"] == "eip3009"
    assert int(acc["amount"]) <= 10000  # $0.01 USDC (6dp) or less


def test_live_sign_against_real_challenge():
    ch = x.fetch_challenge(x.DEX_SEARCH_PATH, {"q": "bnb"})
    acc = x.pick_accept(ch)
    payment, receipt = x.build_payment(ch, acc, _signer(acc["asset"]))
    assert payment["payload"]["signature"].startswith("0x")
    assert payment["network"] == "eip155:8453"
    assert receipt["value"] <= 10000


@pytest.mark.skipif(
    os.environ.get("RUN_X402_SETTLE") != "1",
    reason="set RUN_X402_SETTLE=1 (and fund the Base wallet) to spend real USDC",
)
def test_live_settle_spends_and_returns_data(monkeypatch):
    """Full real settle — spends $0.01 USDC. Requires X402_ENABLED + a funded wallet."""
    monkeypatch.setattr(x.settings, "x402_enabled", True, raising=False)
    data = x.dex_search("bnb")
    assert data and data.get("tks"), f"x402 settle returned no data: {data}"
