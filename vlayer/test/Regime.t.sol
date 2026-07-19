// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.21;

import {Test} from "forge-std/Test.sol";
import {VTest} from "vlayer-0.1.0/testing/VTest.sol";
import {Proof} from "vlayer-0.1.0/Proof.sol";
import {WebProof, WebProofLib} from "vlayer-0.1.0/WebProof.sol";
import {RegimeProver} from "../src/RegimeProver.sol";
import {RegimeVerifier} from "../src/RegimeVerifier.sol";

/// @notice vlayer web-proof tests run under `VTest` (it wires the off-chain prover). This mirrors the
///         `kraken-web-proof` test: load a fixture Web Proof from testdata/, call the prover, then
///         assert the verifier records the attestation. RECONCILE `VTest`/`callProver` helper names
///         against the template output before running `forge test`.
contract RegimeTest is VTest {
    using WebProofLib for WebProof;

    function _fixtureWebProof() internal view returns (WebProof memory) {
        // testdata/fng_webproof.json is produced by the `vlayer` CLI (free, keyless — no header):
        //   vlayer web-proof-fetch --url "https://api.alternative.me/fng/?limit=1" > testdata/fng_webproof.json
        string memory json = vm.readFile("testdata/fng_webproof.json");
        return WebProofLib.fromJson(json); // reconcile helper name with template
    }

    function testProvesAndAttestsFearGreed() public {
        RegimeProver prover = new RegimeProver();
        RegimeVerifier verifier = new RegimeVerifier(address(prover));

        address agent = address(0xA6E);
        bytes32 reportHash = keccak256("regime-report/v1|fixture");

        // Run the prover off-chain (VTest harness) — returns the committed public outputs + Proof.
        callProver();
        (Proof memory proof, address agent_, uint256 fg, string memory cls, bytes32 rh) =
            prover.main(_fixtureWebProof(), agent, reportHash);

        assertEq(agent_, agent);
        assertLe(fg, 100);
        assertEq(rh, reportHash);

        // Submit on-chain; `onlyVerified` accepts because the proof matches main().
        verifier.verify(proof, agent_, fg, cls, rh);

        (uint256 sfg,, uint64 provenAt, bool exists) = verifier.latestOf(agent);
        assertTrue(exists);
        assertEq(sfg, fg);
        assertGt(provenAt, 0);

        (address ra,,,, bool rexists) = verifier.reportProvenance(reportHash);
        assertTrue(rexists);
        assertEq(ra, agent);
    }
}
