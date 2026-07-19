// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.21;

import {Verifier} from "vlayer-0.1.0/Verifier.sol";
import {Proof} from "vlayer-0.1.0/Proof.sol";
import {RegimeProver} from "./RegimeProver.sol";

/// @title RegimeVerifier
/// @notice On-chain registry of vlayer-proven data-provenance attestations. A call only lands if the
///         accompanying vlayer Proof verifies against `RegimeProver.main` (`onlyVerified`), so the
///         stored `fearGreed`/`classification` are guaranteed to have come from the real source
///         (the free alternative.me Fear & Greed endpoint).
///
///         Two lookups:
///           - `latest[agent]`      : the agent's most recent proven regime read (the SIMPLE path)
///           - `byReport[reportHash]`: OPTIONAL — binds a SOLD ERC-8183 deliverable to its proven data
///                                     provenance, so a buyer can verify "this report's inputs are genuine".
contract RegimeVerifier is Verifier {
    address public immutable prover;

    struct Attestation {
        uint256 fearGreed;      // Fear & Greed index (0..100), proven authentic (alternative.me)
        string classification;  // textual classification (alternative.me)
        uint64 provenAt;        // block timestamp when the proof was verified
        address agent;          // the selling agent's identity address
        bool exists;
    }

    /// @notice latest proven attestation per agent
    mapping(address => Attestation) public latest;
    /// @notice attestation bound to a specific sold deliverable (keccak256 of the report)
    mapping(bytes32 => Attestation) public byReport;

    event RegimeProven(
        address indexed agent,
        uint256 fearGreed,
        string classification,
        bytes32 indexed reportHash,
        uint64 provenAt
    );

    constructor(address _prover) {
        prover = _prover;
    }

    /// @notice Record a proven regime attestation. The argument list AFTER `Proof` must match the
    ///         non-Proof return values of `RegimeProver.main` exactly — vlayer's `onlyVerified`
    ///         checks the proof commits to precisely these public inputs/outputs.
    function verify(
        Proof calldata,
        address agent,
        uint256 fearGreed,
        string calldata classification,
        bytes32 reportHash
    ) public onlyVerified(prover, RegimeProver.main.selector) {
        Attestation memory a = Attestation({
            fearGreed: fearGreed,
            classification: classification,
            provenAt: uint64(block.timestamp),
            agent: agent,
            exists: true
        });

        latest[agent] = a;
        if (reportHash != bytes32(0)) {
            byReport[reportHash] = a;
        }

        emit RegimeProven(agent, fearGreed, classification, reportHash, a.provenAt);
    }

    /// @notice Convenience read for the Python provenance bridge / dashboard (no key needed).
    function latestOf(address agent)
        external
        view
        returns (uint256 fearGreed, string memory classification, uint64 provenAt, bool exists)
    {
        Attestation storage a = latest[agent];
        return (a.fearGreed, a.classification, a.provenAt, a.exists);
    }

    /// @notice Verify a specific sold report's provenance by its keccak256 hash.
    function reportProvenance(bytes32 reportHash)
        external
        view
        returns (address agent, uint256 fearGreed, string memory classification, uint64 provenAt, bool exists)
    {
        Attestation storage a = byReport[reportHash];
        return (a.agent, a.fearGreed, a.classification, a.provenAt, a.exists);
    }
}
