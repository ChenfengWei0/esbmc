// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// SSA-cost anchor (Stage 0) — FAIL variant (CORE).
//
// Same shape as ssa_cost_bytes32_push_knownbug with a planted bug:
// arr[base+7] is bytes32(8), not bytes32(99). Falsifies at k=1 base
// case (fast verdict; no inductive step needed); pairs with the
// PASS-variant KNOWNBUG anchor that times out on inductive step.
// Locks regression coverage of the FAIL pathway across Stage 1+
// changes to _ESBMC_array_push.
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
        // FLIPPED: arr[base+7] is 8, not 99
        assert(arr[base + 7] == bytes32(uint256(99)));
    }
}
