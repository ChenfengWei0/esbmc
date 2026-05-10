// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// SSA-cost anchor (Stage 0): bytes32 INNER-array push stress.
//
// Uses bytes32[][] (nested), so inner-array pushes route through the
// generic _ESBMC_array_push helper (per-byte memcpy preservation
// chain) — the actual hotspot identified by Stage 0 measurement
// (~34% of post-slice live SSA on napp_struct_multifield_fail).
//
// Top-level bytes32[] would use the state-var inline path which
// doesn't exercise _ESBMC_array_push at all.
//
// KNOWNBUG until typed-element push for 32-byte elements
// (Stage 1.a) reduces per-push cost from ~64 to ~3 SSA.
//
// Asserts only on the LAST pushed slot to match the existing
// _ESBMC_array_push_uint256 semantic (stale-slot approximation
// for older indices is sound over-approximation).
contract C {
    bytes32[][] internal arr;

    function run() external {
        if (arr.length == 0)
            arr.push();
        uint256 base = arr[0].length;
        arr[0].push(bytes32(uint256(1)));
        arr[0].push(bytes32(uint256(2)));
        arr[0].push(bytes32(uint256(3)));
        arr[0].push(bytes32(uint256(4)));
        arr[0].push(bytes32(uint256(5)));
        arr[0].push(bytes32(uint256(6)));
        arr[0].push(bytes32(uint256(7)));
        arr[0].push(bytes32(uint256(8)));
        assert(arr[0].length == base + 8);
        assert(arr[0][base + 7] == bytes32(uint256(8)));
    }
}
