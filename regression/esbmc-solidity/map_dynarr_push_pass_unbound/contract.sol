// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// KNOWNBUG (2026-04-21): same limitation as `map_dynarr_push_pass_bound`
// — see its comment for the full story. Under --unbound the initial
// state is even more obviously nondet, so the fresh-malloc helper
// can't refute a model where the pushed slot is read as nondet.
//
// Unbound dual of map_dynarr_push_pass_bound: push+read happens inside
// a single function call, so the state write is visible to the
// subsequent read regardless of the nondet-init of the state var in
// --unbound mode.
contract C {
    mapping(address => uint256[]) m;
    function test() public {
        m[address(0x1)].push(42);
        assert(m[address(0x1)][0] == 42);
    }
}
