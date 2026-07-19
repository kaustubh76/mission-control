"""vlayer provenance wiring — pins the Python read-ABI to the Solidity contract + canonical hashing.

Guards the "everything wired up" contract OFFLINE (no chain / no vlayerup): the read ABI in
`provenance.py` must stay in lockstep with `RegimeVerifier.sol`'s `latestOf`/`reportProvenance`,
and the optional report-hash binding must be deterministic + single-sourced."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ictbot.agent import provenance

VERIFIER_SOL = Path(__file__).resolve().parent.parent / "vlayer" / "src" / "RegimeVerifier.sol"


def _has_web3() -> bool:
    try:
        import web3  # noqa: F401

        return True
    except Exception:
        return False


def _abi_fn(name: str) -> dict:
    return next(f for f in provenance._VERIFIER_ABI if f.get("name") == name)


# --------------------------- ABI ↔ contract shape ------------------------- #
def test_abi_read_functions_have_expected_shape():
    lo = _abi_fn("latestOf")
    assert [o["type"] for o in lo["outputs"]] == ["uint256", "string", "uint64", "bool"]
    assert lo["inputs"][0]["type"] == "address" and lo["stateMutability"] == "view"
    rp = _abi_fn("reportProvenance")
    assert [o["type"] for o in rp["outputs"]] == ["address", "uint256", "string", "uint64", "bool"]
    assert rp["inputs"][0]["type"] == "bytes32" and rp["stateMutability"] == "view"


def test_abi_output_names_match_contract_source():
    """Cross-check the Python ABI output names against the Solidity source — catches drift if either
    side is edited without the other."""
    if not VERIFIER_SOL.exists():
        pytest.skip("contract source not present")
    src = VERIFIER_SOL.read_text()
    for fn, expected in (
        ("latestOf", ["fearGreed", "classification", "provenAt", "exists"]),
        ("reportProvenance", ["agent", "fearGreed", "classification", "provenAt", "exists"]),
    ):
        m = re.search(rf"function {fn}\([^)]*\)\s*external\s*view\s*returns\s*\(([^)]*)\)", src)
        assert m, f"{fn} signature not found in {VERIFIER_SOL.name}"
        sol_names = [p.strip().split()[-1] for p in m.group(1).split(",")]
        abi_names = [o["name"] for o in _abi_fn(fn)["outputs"]]
        assert sol_names == abi_names == expected, f"{fn}: sol={sol_names} abi={abi_names}"


# --------------------------- canonical report json ------------------------ #
def test_canonical_report_json_is_deterministic_and_sorted():
    r1 = {"b": 1, "a": [3, 2], "z": "x"}
    r2 = {"z": "x", "a": [3, 2], "b": 1}  # same content, different insertion order
    assert provenance.canonical_report_json(r1) == provenance.canonical_report_json(r2)
    assert provenance.canonical_report_json(r1) == '{"a":[3,2],"b":1,"z":"x"}'  # sorted, compact


@pytest.mark.skipif(not _has_web3(), reason="web3 not installed")
def test_report_hash_stable_hex_and_single_sourced():
    h = provenance.report_hash({"a": 1, "b": 2})
    assert isinstance(h, str) and h.startswith("0x") and len(h) == 66
    assert provenance.report_hash({"b": 2, "a": 1}) == h  # order-independent (canonical) + deterministic


# --------------------------- gating (simple path, off) -------------------- #
def test_available_off_by_default(monkeypatch):
    monkeypatch.setattr(provenance.settings, "vlayer_enabled", False)
    monkeypatch.setattr(provenance.settings, "vlayer_verifier_address", "")
    assert provenance.available() is False
    assert provenance.latest_attestation() is None  # no network read when off
