// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// SSA-cost anchor (Stage 0): bytes32 push stress.
//
// Documents that bytes32 push currently emits ~64 SSA per call via
// per-byte memcpy in _ESBMC_array_push. With 8 pushes per dispatcher
// iteration × k-induction's 12-step ladder (k=1..25 step 2), the
// formula scales out of the 60s ctest budget. KNOWNBUG until typed-
// element push for 32-byte elements (Stage 1.a) reduces per-push
// cost to ~3 SSA, enabling fast verdict.
//
// Uses entry-relative assertions so dispatcher's while(nondet) loop
// does not invalidate state across iterations.
contract C {
    bytes32[] internal arr;

    function run() external {
        uint256 base = arr.length;
        arr.push(bytes32(uint256(1)));
        arr.push(bytes32(uint256(2)));
        arr.push(bytes32(uint256(3)));
        arr.push(bytes32(uint256(4)));
        arr.push(bytes32(uint256(5)));
        arr.push(bytes32(uint256(6)));
        arr.push(bytes32(uint256(7)));
        arr.push(bytes32(uint256(8)));
        assert(arr.length == base + 8);
        assert(arr[base] == bytes32(uint256(1)));
        assert(arr[base + 3] == bytes32(uint256(4)));
        assert(arr[base + 7] == bytes32(uint256(8)));
    }
}
