// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// T2.1 Stage S0 — KNOWNBUG tracking B7-C (bytes-arg distinguishability).
// This test asserts that abi.encode(uint256, bytes) distinguishes by the
// bytes content (length differs across the two calls).
//
// EXPECTED TO STAY KNOWNBUG after T2.1 S1: the multi-arg fold lands but
// `t_bytes_*` args remain skipped (struct-member access not in scope).
// Will flip to CORE in a separate B7-C stage that extends the fold to
// extract BytesDynamic.length / .offset and combine into the result.
contract H {
    function check(uint256 a, bytes calldata b1, bytes calldata b2) external pure {
        require(b1.length != b2.length);
        bytes32 h1 = keccak256(abi.encode(a, b1));
        bytes32 h2 = keccak256(abi.encode(a, b2));
        assert(h1 != h2);
    }
}
