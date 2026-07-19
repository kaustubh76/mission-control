"""Tests for the keyless Multicall3 balance reader (the live-trading read source).

These exercise `read_onchain_balances` — the fresh, no-pricing, fail-fast counterpart to
`wallet_card` that the LIVE CliTwakClient uses so the daemon stops depending on the flaky
`twak balance` RPC. The web3 layer is mocked (no real RPC), but the real eth_abi
encode/decode runs so the decimal/symbol mapping is genuinely exercised.
"""

from __future__ import annotations

import types

import pytest
from eth_abi import encode as abi_encode

from ictbot.api import onchain


def _mk_w3(res, *, raise_on_call=False):
    """A minimal web3 stand-in: w3.eth.contract(...).functions.aggregate3(calls).call() -> res."""

    def _call():
        if raise_on_call:
            raise RuntimeError("multicall boom")
        return res

    agg = lambda calls: types.SimpleNamespace(call=_call)  # noqa: E731
    contract = lambda address=None, abi=None: types.SimpleNamespace(  # noqa: E731
        functions=types.SimpleNamespace(aggregate3=agg)
    )
    return types.SimpleNamespace(eth=types.SimpleNamespace(contract=contract, block_number=123))


def _u256(v: int) -> tuple[bool, bytes]:
    return (True, abi_encode(["uint256"], [v]))


_ADDR = "0x000000000000000000000000000000000000dEaD"


def test_reads_native_bnb_and_maps_decimals(monkeypatch):
    # tok order after skipping WBNB/USDC: USDT, ETH, CAKE, LINK, UNI, AVAX, DOT, DOGE.
    res = [
        _u256(int(0.002655 * 1e18)),  # [0] native BNB (getEthBalance, 18dp)
        _u256(int(5.374486 * 1e18)),  # USDT  (18dp)
        _u256(0),  # ETH
        _u256(0),  # CAKE
        _u256(0),  # LINK
        _u256(int(1.845348 * 1e18)),  # UNI   (18dp)
        _u256(0),  # AVAX
        _u256(0),  # DOT
        _u256(int(10.0 * 1e8)),  # DOGE  (8dp! — decimals must be honored)
    ]
    monkeypatch.setattr(onchain, "_connect", lambda: _mk_w3(res))
    out = onchain.read_onchain_balances(_ADDR)

    assert out["BNB"] == pytest.approx(0.002655, abs=1e-9)
    assert out["USDT"] == pytest.approx(5.374486, abs=1e-9)
    assert out["UNI"] == pytest.approx(1.845348, abs=1e-9)
    assert out["DOGE"] == pytest.approx(10.0, abs=1e-9)  # 8dp normalized correctly
    assert out["ETH"] == 0.0


def test_excludes_wbnb_and_usdc(monkeypatch):
    # native BNB + 8 trading tokens = 9 results; WBNB/USDC must never appear (book holds native BNB).
    res = [_u256(0)] + [_u256(0)] * 8
    monkeypatch.setattr(onchain, "_connect", lambda: _mk_w3(res))
    out = onchain.read_onchain_balances(_ADDR)

    assert "WBNB" not in out and "USDC" not in out
    assert set(out) == {"BNB", "USDT", "ETH", "CAKE", "LINK", "UNI", "AVAX", "DOT", "DOGE"}


def test_raises_when_no_rpc(monkeypatch):
    # Fail-fast: a sustained RPC outage must RAISE (so the caller skips the tick), not return {}.
    monkeypatch.setattr(onchain, "_connect", lambda: None)
    with pytest.raises(RuntimeError, match="no reachable BSC RPC"):
        onchain.read_onchain_balances(_ADDR)


def test_raises_when_multicall_throws(monkeypatch):
    monkeypatch.setattr(onchain, "_connect", lambda: _mk_w3([], raise_on_call=True))
    with pytest.raises(RuntimeError, match="multicall failed"):
        onchain.read_onchain_balances(_ADDR)


def test_raises_without_address(monkeypatch):
    monkeypatch.setattr(onchain.settings, "agent_trading_address", "", raising=False)
    with pytest.raises(RuntimeError, match="no address"):
        onchain.read_onchain_balances(None)


def test_tolerates_individual_failed_call(monkeypatch):
    # allowFailure semantics: one (ok=False) entry decodes to 0, never crashes the whole read.
    res = [(False, b"")] + [_u256(int(3.0 * 1e18))] + [_u256(0)] * 7  # BNB call failed; USDT ok
    monkeypatch.setattr(onchain, "_connect", lambda: _mk_w3(res))
    out = onchain.read_onchain_balances(_ADDR)
    assert out["BNB"] == 0.0
    assert out["USDT"] == pytest.approx(3.0, abs=1e-9)
