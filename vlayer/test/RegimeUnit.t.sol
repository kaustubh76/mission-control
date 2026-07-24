// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.21;

import {Test} from "forge-std/Test.sol";
import {RegimeProver} from "../src/RegimeProver.sol";
import {RegimeVerifier} from "../src/RegimeVerifier.sol";

/// @dev Exposes RegimeProver's internal Fear & Greed parser for direct unit testing.
contract RegimeProverHarness is RegimeProver {
    function parseUint(string memory s) external pure returns (uint256) {
        return _parseUint(s);
    }
}

/// @notice Prover-independent unit tests — validate the Fear & Greed parsing bounds and the verifier's
///         storage/views WITHOUT a web proof. (The full notarize -> prove -> verify path is covered by
///         Regime.t.sol under `vlayer test`.) These run under plain `forge test`.
contract RegimeUnitTest is Test {
    RegimeProverHarness internal prover;
    RegimeVerifier internal verifier;

    function setUp() public {
        prover = new RegimeProverHarness();
        verifier = new RegimeVerifier(address(prover));
    }

    // ---- Fear & Greed parsing (alternative.me returns `value` as a quoted integer string) ----

    function test_parseUint_typicalValues() public view {
        assertEq(prover.parseUint("0"), 0);
        assertEq(prover.parseUint("25"), 25);
        assertEq(prover.parseUint("100"), 100);
    }

    function test_parseUint_rejectsEmpty() public {
        vm.expectRevert(bytes("fng: bad length"));
        prover.parseUint("");
    }

    function test_parseUint_rejectsTooLong() public {
        vm.expectRevert(bytes("fng: bad length"));
        prover.parseUint("1000");
    }

    function test_parseUint_rejectsNonDigit() public {
        vm.expectRevert(bytes("fng: non-digit"));
        prover.parseUint("2a");
    }

    // ---- Verifier wiring + default (unattested) state ----

    function test_verifier_bindsProver() public view {
        assertEq(verifier.prover(), address(prover));
    }

    function test_latestOf_unattestedIsEmpty() public view {
        (uint256 fg,, uint64 provenAt, bool exists) = verifier.latestOf(address(0xABCD));
        assertEq(fg, 0);
        assertEq(provenAt, 0);
        assertTrue(!exists);
    }

    function test_reportProvenance_unboundIsEmpty() public view {
        (address agent,,,, bool exists) = verifier.reportProvenance(keccak256("nope"));
        assertEq(agent, address(0));
        assertTrue(!exists);
    }
}
