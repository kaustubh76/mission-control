"""
Opt-in REAL NodeReal/MegaFuel link test — hits the live keyed endpoint.

Skipped by default. Activate with:
    RUN_NODEREAL_INTEGRATION=1 pytest -q tests/test_nodereal_real_integration.py

READ-ONLY (eth_chainId / eth_getTransactionCount / pm_isSponsorable) — no mint, no
spend. Each call lands on the configured NodeReal dashboard. Verifies that the keyed
endpoint our code builds is reachable and reports the correct chain per network.
Requires NODEREAL_API_KEY in .env.
"""

from __future__ import annotations

import os

import pytest

from ictbot.agent import identity
from ictbot.settings import settings

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_NODEREAL_INTEGRATION") != "1",
    reason="set RUN_NODEREAL_INTEGRATION=1 to hit the live NodeReal/MegaFuel endpoint",
)


def _require_key():
    if not settings.nodereal_api_key:
        pytest.skip("NODEREAL_API_KEY not set")


def test_live_testnet_link_reaches_chain_97():
    _require_key()
    r = identity.verify_paymaster_link("bsc-testnet")
    assert r["reachable"] is True, r
    assert r["chain_id"] == 97 and r["chain_ok"] is True
    # sponsorable may be True or False depending on the dashboard policy — just present.
    assert "sponsorable" in r


def test_live_mainnet_link_reaches_chain_56():
    _require_key()
    r = identity.verify_paymaster_link("bsc")
    assert r["reachable"] is True, r
    assert r["chain_id"] == 56 and r["chain_ok"] is True
