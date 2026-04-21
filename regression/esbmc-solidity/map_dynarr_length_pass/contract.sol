// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// KNOWNBUG (2026-04-21): `mapping(K => V[]).push(x)` write-through
// tracks the new data pointer in the mapping slot, and the new
// allocation's header is set to new_len. But between pushes the
// typed helper (`_ESBMC_array_push_uint256`) allocates a FRESH slab
// instead of realloc — so `_hdr_read` of the previous allocation is
// read correctly, but the old data region's element bytes are
// NOT carried over. For the length-only assert here, the failure is
// the same root cause as map_dynarr_push_pass_bound: initial state
// nondet admits models where `_hdr_read(m[k])` doesn't match the
// observed push count. See CLAUDE_Solidity.md §F.2.
//
// Length after N pushes: three pushes must yield length == 3.
contract C {
    mapping(address => uint256[]) m;
    function test() public {
        m[address(0x1)].push(1);
        m[address(0x1)].push(2);
        m[address(0x1)].push(3);
        assert(m[address(0x1)].length == 3);
    }
}
