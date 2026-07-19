// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.21;

import {Prover} from "vlayer-0.1.0/Prover.sol";
import {Proof} from "vlayer-0.1.0/Proof.sol";
import {Web, WebProof, WebProofLib, WebLib} from "vlayer-0.1.0/WebProof.sol";

/// @title RegimeProver
/// @notice Proves that a FREE, keyless Fear & Greed HTTPS response was genuinely served by
///         `api.alternative.me` (zkTLS Web Proof) and extracts the index value. The agent's regime
///         read is sourced entirely from free, keyless public APIs.
///
/// Lead endpoint = alternative.me Fear & Greed (no API key, public):
///   GET https://api.alternative.me/fng/?limit=1
///   => { "data": [ { "value": "25", "value_classification": "Extreme Fear", ... } ], ... }
///   NOTE `value` is a QUOTED integer string ("25"), so we read it as a string and parse the digits
///   to a uint (no float parsing — the value is a whole number 0..100).
///
/// The proven `agent` and `reportHash` are echoed through as public inputs so the Verifier can bind
/// the attestation to (a) the selling agent and (b) the exact ERC-8183 deliverable it sold.
///
/// @dev Reconcile import version strings (`vlayer-0.1.0/...`) and `WebLib` signatures against a fresh
///      `vlayer init --template kraken-web-proof` before the first `forge build` (see vlayer/README.md).
contract RegimeProver is Prover {
    using WebProofLib for WebProof;
    using WebLib for Web;

    /// @notice The exact URL whose TLS transcript is notarized. MUST match the URL passed to the
    ///         notary in prove.ts, or `webProof.verify` reverts. Free, keyless — no header needed.
    string public constant DATA_URL = "https://api.alternative.me/fng/?limit=1";

    /// @notice Verify the Web Proof and return the extracted, provenance-bound regime inputs.
    /// @param webProof   the zkTLS transcript + notary signature produced off-chain
    /// @param agent      the selling agent's identity address (public input, echoed through)
    /// @param reportHash keccak256 of the ERC-8183 deliverable this data backs (0x0 if unbound)
    /// @return proof     the vlayer proof commitment consumed by the Verifier's `onlyVerified`
    /// @return agent_    echoed agent address
    /// @return fearGreed Fear & Greed index (0..100)
    /// @return classification textual classification (e.g. "Extreme Fear")
    /// @return reportHash_ echoed report hash
    function main(WebProof calldata webProof, address agent, bytes32 reportHash)
        public
        view
        returns (Proof memory, address agent_, uint256 fearGreed, string memory classification, bytes32 reportHash_)
    {
        // 1. Verify the notarized transcript really came from DATA_URL over TLS.
        Web memory web = webProof.verify(DATA_URL);

        // 2. alternative.me nests the record in a 1-element array and quotes the value as a string,
        //    so read it as a string at `data.0.value` and parse the ASCII digits to a uint.
        uint256 fg = _parseUint(web.jsonGetString("data.0.value"));
        require(fg <= 100, "fng: out of range");
        string memory cls = web.jsonGetString("data.0.value_classification");

        // 3. Commit the outputs (agent + reportHash are public inputs bound into the proof).
        return (proof(), agent, fg, cls, reportHash);
    }

    /// @dev Parse a decimal-digit string (e.g. "25") to a uint256. Reverts on any non-digit byte, so
    ///      a malformed value cannot silently attest a wrong number. The F&G value is 0..100.
    function _parseUint(string memory s) internal pure returns (uint256) {
        bytes memory b = bytes(s);
        require(b.length > 0 && b.length <= 3, "fng: bad length");
        uint256 out = 0;
        for (uint256 i = 0; i < b.length; i++) {
            uint8 c = uint8(b[i]);
            require(c >= 0x30 && c <= 0x39, "fng: non-digit");
            out = out * 10 + (c - 0x30);
        }
        return out;
    }
}
