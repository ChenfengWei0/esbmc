// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// SSA-cost anchor (Stage 0) — FAIL variant (CORE).
//
// Same nested-array shape as ssa_cost_bytes32_push_knownbug with a
// planted bug: arr[0][base+7] is bytes32(8), not bytes32(99).
// Falsifies at k=1 base case (fast verdict; no inductive step needed).
// Locks regression coverage of the FAIL pathway across Stage 1+
// changes to _ESBMC_array_push.
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
        // FLIPPED: arr[0][base+7] is 8, not 99
        assert(arr[0][base + 7] == bytes32(uint256(99)));
    }
}
