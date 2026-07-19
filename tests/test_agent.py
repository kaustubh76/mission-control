"""Tests for the agent layer — NL strategy spec, decision rationale, identity."""

from __future__ import annotations

import pytest

from ictbot.agent import identity, rationale, strategy_spec
from ictbot.strategy.momentum_allocator import AllocatorParams


def test_strategy_spec_parses_committed_config():
    p, floor, ceiling = strategy_spec.load_spec()  # reads config/strategy.md
    assert p.top_k == 5
    assert p.lookback == 120
    assert p.inverse_vol is True
    assert p.rebal_bars == 6
    assert (floor, ceiling) == (0.35, 0.80)


def test_strategy_spec_overrides_from_natural_language():
    text = (
        "Hold the top 3 tokens by 60-bar momentum, inverse-vol weighted, "
        "deploy between 30% and 70%, rebalance daily."
    )
    p, floor, ceiling = strategy_spec.parse_spec(text)
    assert p.top_k == 3
    assert p.lookback == 60
    assert (floor, ceiling) == (0.30, 0.70)


def test_strategy_spec_garbage_falls_back_to_defaults():
    p, floor, ceiling = strategy_spec.parse_spec("no parseable knobs here")
    d = AllocatorParams()
    assert (p.top_k, p.lookback) == (d.top_k, d.lookback)
    assert (floor, ceiling) == (0.40, 0.85)


def test_strategy_summary_is_human_readable():
    s = strategy_spec.summary()
    assert "top-5" in s and "120-bar" in s and "TWAK" in s


def test_rationale_all_cash_explains_the_wait():
    txt = rationale.explain(fear_greed=15, regime_score=0.05, deploy_cap=0.42, weights={})
    assert "extreme fear" in txt
    assert "risk-off" in txt
    assert "100% USDT" in txt


def test_rationale_deployed_lists_holdings():
    txt = rationale.explain(
        fear_greed=62, regime_score=0.74, deploy_cap=0.78, weights={"BNB": 0.45, "ETH": 0.33}
    )
    assert "greed" in txt
    assert "BNB" in txt and "ETH" in txt
    assert "USDT" in txt


def test_rationale_handles_missing_sentiment():
    txt = rationale.explain(fear_greed=None, regime_score=0.3, deploy_cap=0.5, weights={})
    assert "unavailable" in txt


def test_identity_profile_is_key_free_and_describes_the_agent():
    prof = identity.profile()
    assert prof["name"] == "RegimeAdaptiveMomentumAgent"
    assert prof["network"] in ("bsc-testnet", "bsc")
    assert prof["endpoints"] and "spot-swap" in prof["endpoints"][0]["capabilities"]


def test_identity_register_requires_wallet_password(monkeypatch):
    # no private key needed (bnbagent self-manages the wallet) — but a keystore
    # password is required; registration must refuse cleanly, not crash.
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_wallet_password", "", raising=False)
    with pytest.raises(RuntimeError, match="WALLET_PASSWORD"):
        identity.register_identity()


def test_identity_profile_links_trading_wallet(monkeypatch):
    monkeypatch.setattr(
        "ictbot.agent.identity.settings.agent_trading_address",
        "0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215",
        raising=False,
    )
    prof = identity.profile()
    assert prof["trading_wallet"] == "0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215"
    assert "0xE8A30d24BbA030D3e8a844bD1c4F6e1374EA6215" in prof["description"]


# ---- NodeReal/MegaFuel keyed-endpoint wiring (the zero-requests root-cause fix) ----


def test_sdk_network_routes_through_keyed_nodereal_endpoint(monkeypatch):
    """With NODEREAL_API_KEY set, _sdk_network() returns a NetworkConfig whose
    paymaster_url is the user's KEYED sponsor endpoint (not the public one) — so
    every gasless write lands in their NodeReal app. This is the core fix."""
    monkeypatch.setattr("ictbot.agent.identity.settings.nodereal_api_key", "KEY123", raising=False)
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_network", "bsc", raising=False)
    monkeypatch.setattr(
        "ictbot.agent.identity.settings.bsc_rpc_https_url", "https://bsc.example/abc", raising=False
    )
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_use_paymaster", True, raising=False)
    nc = identity._sdk_network()
    assert not isinstance(nc, str)  # a NetworkConfig instance, not a preset name
    assert nc.chain_id == 56  # mainnet
    assert "KEY123" in nc.paymaster_url and "megafuel/56" in nc.paymaster_url
    assert nc.paymaster_url.startswith("https://open-platform-ap.nodereal.io/")
    assert nc.rpc_url == "https://bsc.example/abc"  # keyed RPC honored
    assert nc.use_paymaster is True
    assert nc.registry_contract  # mainnet registry preserved from preset


def test_sdk_network_falls_back_to_public_preset_without_key(monkeypatch):
    """No key -> return the preset NAME (string), which the SDK resolves to the
    PUBLIC paymaster. (Maps settings 'bsc' -> SDK preset 'bsc-mainnet'.)"""
    monkeypatch.setattr("ictbot.agent.identity.settings.nodereal_api_key", "", raising=False)
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_network", "bsc", raising=False)
    assert identity._sdk_network() == "bsc-mainnet"


def test_write_heartbeat_noops_without_agent_id(monkeypatch):
    """Heartbeat is best-effort: no agent_id -> returns None, never raises (must
    not break a trading tick)."""
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_id", 0, raising=False)
    assert identity.write_heartbeat("a rationale", 1234.5) is None


class _FakeAgent:
    """Stand-in for ERC8004Agent: set_metadata can succeed/raise; get_metadata returns a blob."""

    def __init__(self, *, tx="0xfeed", raise_on_write=False, metadata=None):
        self._tx, self._raise, self._meta = tx, raise_on_write, metadata

    def set_metadata(self, agent_id, key, value):
        if self._raise:
            raise RuntimeError("insufficient funds for gas * price + value")
        return {"transactionHash": self._tx}

    def get_metadata(self, agent_id, key):
        return self._meta


def _arm_heartbeat(monkeypatch, agent):
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_id", 133085, raising=False)
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_wallet_password", "pw", raising=False)
    monkeypatch.setattr("ictbot.agent.identity.available", lambda: True, raising=False)
    monkeypatch.setattr("ictbot.agent.identity._agent", lambda *a, **k: agent, raising=False)


def test_write_heartbeat_success_returns_structured(monkeypatch):
    """A successful set_metadata returns {ok:True, tx:...} — not a bare SDK dict."""
    _arm_heartbeat(monkeypatch, _FakeAgent(tx="0xabc123"))
    res = identity.write_heartbeat("greed; deploy 60%", 1000.0)
    assert res and res["ok"] is True and res["tx"] == "0xabc123"


def test_write_heartbeat_failure_surfaces_reason_not_none(monkeypatch):
    """The fix: a failing write SURFACES the reason ({ok:False, error}) instead of the old silent
    None — so an unfunded wallet / 403 is actionable. Still never raises."""
    _arm_heartbeat(monkeypatch, _FakeAgent(raise_on_write=True))
    res = identity.write_heartbeat("greed; deploy 60%", 1000.0)
    assert res is not None and res["ok"] is False
    assert "insufficient funds" in res["error"]


def test_read_heartbeat_parses_onchain_blob(monkeypatch):
    """read_heartbeat verifies a heartbeat landed by parsing the on-chain metadata blob."""
    blob = '{"ts": "2026-06-22T00:00:00Z", "nav": 1234.5, "rationale": "fear; 40% cap"}'
    _arm_heartbeat(monkeypatch, _FakeAgent(metadata=blob))
    hb = identity.read_heartbeat()
    assert hb and hb["nav"] == 1234.5 and "fear" in hb["rationale"]


def test_read_heartbeat_noops_without_agent_id(monkeypatch):
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_id", 0, raising=False)
    assert identity.read_heartbeat() is None


def test_heartbeat_gas_ready_direct_gas_reports_funding(monkeypatch):
    """Direct-gas readiness: ready only when the identity wallet BNB clears the floor; otherwise it
    returns an actionable 'fund X with >= Y BNB' detail (the antidote to silent failure)."""
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_use_paymaster", False, raising=False)
    monkeypatch.setattr("ictbot.agent.identity.display_address", lambda: "0xEb7b", raising=False)
    monkeypatch.setattr("ictbot.agent.identity.identity_wallet_bnb", lambda *a, **k: 0.0, raising=False)
    out = identity.heartbeat_gas_ready()
    assert out["mode"] == "direct-gas" and out["ready"] is False and "fund" in out["detail"]
    monkeypatch.setattr("ictbot.agent.identity.identity_wallet_bnb", lambda *a, **k: 0.5, raising=False)
    assert identity.heartbeat_gas_ready()["ready"] is True


def test_display_address_is_key_free_with_public_address(monkeypatch):
    """SECURITY: a deployed read-only dashboard shows the wallet via the PUBLIC
    AGENT_IDENTITY_ADDRESS with NO key/password — display_address never derives, so
    no fund-controlling secret is needed in the cloud."""
    addr = "0xEb7bF36aab4912c955474206EF0b835170389655"
    monkeypatch.setattr(
        "ictbot.agent.identity.settings.agent_identity_address", addr, raising=False
    )
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_wallet_password", "", raising=False)
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_private_key", "", raising=False)
    assert identity.display_address() == addr
    assert identity._identity_address() is None  # no key present → cannot derive
    from ictbot.data import x402_cmc

    assert x402_cmc.payment_address() == addr  # x402 display rides on it → key-free too


def test_keyed_paymaster_url_maps_network_to_segment(monkeypatch):
    """The keyed endpoint carries the API key and the right CAIP segment per network."""
    monkeypatch.setattr("ictbot.agent.identity.settings.nodereal_api_key", "KEY9", raising=False)
    assert identity._preset_for("bsc") == "bsc-mainnet"
    assert identity._preset_for("bsc-testnet") == "bsc-testnet"
    main = identity._keyed_paymaster_url("bsc-mainnet")
    test = identity._keyed_paymaster_url("bsc-testnet")
    assert main.startswith("https://open-platform-ap.nodereal.io/KEY9/") and main.endswith(
        "/megafuel/56"
    )
    assert test.endswith("/megafuel-testnet/97")
    assert identity._mask_key(main) == "https://open-platform-ap.nodereal.io/****/megafuel/56"


def test_verify_paymaster_link_no_key_is_offline(monkeypatch):
    """Without a key, verify reports unreachable without any network call."""
    monkeypatch.setattr("ictbot.agent.identity.settings.nodereal_api_key", "", raising=False)
    r = identity.verify_paymaster_link("bsc")
    assert r["reachable"] is False and "NODEREAL_API_KEY" in r["error"]


def test_payment_address_deterministic_for_pinned_key(monkeypatch, tmp_path):
    """x402/identity pay address is derived from the wallet key — deterministic for a
    pinned AGENT_PRIVATE_KEY (Hardhat acct #0), and None without a password.

    Isolates the keystore dir to tmp_path so this NEVER writes to the real
    ~/.bnbagent/wallets/ (a second keystore there would break _identity_address)."""
    import bnbagent.wallets.evm_wallet_provider as evm

    monkeypatch.setattr(evm, "_WALLETS_DIR", tmp_path, raising=False)
    from ictbot.data import x402_cmc

    # Blank the PUBLIC display address: a real .env sets AGENT_IDENTITY_ADDRESS (the
    # --pin-key flow writes it), and display_address() would short-circuit to it —
    # this test is specifically about DERIVING the address from the pinned key.
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_identity_address", "", raising=False)
    key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_wallet_password", "pw", raising=False)
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_private_key", key, raising=False)
    assert x402_cmc.payment_address() == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    monkeypatch.setattr("ictbot.agent.identity.settings.agent_wallet_password", "", raising=False)
    assert x402_cmc.payment_address() is None
