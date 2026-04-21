// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// KNOWNBUG (2026-04-21): `mapping(K => uint256[]).push(x)` write-through
// is partially wired (`_ESBMC_array_push_uint256` routes the new data
// pointer back into the mapping slot), but the helper uses a fresh
// malloc without copying the previous allocation's bytes. For the pass
// side of this test, the solver can pick an initial state where the
// mapping slot is a nondet pointer (not strictly NULL in the infinite
// SMT array model), so the post-push read falls back to nondet.
// The fail-side dual (`map_dynarr_push_fail_bound`) passes CORE because
// a FAIL outcome from the nondet read is consistent with the
// over-approximation. Full fix requires modelling the value slot as a
// nested infinite SMT array + per-key length tracker (similar to
// `#sol_dynarray_state`), see CLAUDE_Solidity.md §F.2.
//
// Write-through semantics for mapping(K => V[]).push(x).
// After the push, m[a][0] must equal the pushed value.
contract C {
    mapping(address => uint256[]) m;
    function test() public {
        m[address(0x1)].push(42);
        assert(m[address(0x1)][0] == 42);
    }
}
