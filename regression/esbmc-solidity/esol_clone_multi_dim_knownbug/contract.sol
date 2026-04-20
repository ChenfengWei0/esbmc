// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
// KNOWNBUG: multi-dim fixed array `uint256[M][N]` does not round-trip
// correctly under the ctor model even in the SINGLE-INSTANCE case
// across two separate contract instances.  `new C()` calloc's only
// the outer pointer array (N slots of `uint256*`); the inner M-row
// buffers are left unallocated.  Writes through `c.grid[i][j]`
// succeed (under `--no-standard-checks`) to a nondet location but
// subsequent reads from a DIFFERENT contract instance against an
// uninitialised inner pointer return nondet values.
//
// The deep-copy walker (Phase 1) correctly reallocates the OUTER
// array via _ESBMC_arrcpy and, when needs_clone_deep_fixup detects
// the non-scalar element type, unrolls per-element recursion.  The
// recursion, however, relies on base's inner rows being valid — and
// they aren't.  The underlying fix is to extend the ctor so it
// recursively calloc's nested pointer-backed storage for every
// state-var field.
//
// This test documents the current state by asserting a property that
// WOULD hold if the ctor were fixed (cross-instance isolation of an
// unwritten cell reads 0), so it flips from KNOWNBUG to PASS once
// that fix lands.
function __ESOL_deep_copy(C src) pure returns (C) { return src; }

contract C {
    uint256[2][3] public grid;
    function setAt(uint256 i, uint256 j, uint256 v) public { grid[i][j] = v; }
    function get(uint256 i, uint256 j) public view returns (uint256) { return grid[i][j]; }
}

contract H {
    function check(uint256 a) public {
        require(a != 0);
        C base = new C();
        base.setAt(0, 0, a);
        C clone = __ESOL_deep_copy(base);
        // Deep-copied clone should see a at (0,0).  Currently fails
        // because clone's grid outer is fresh but inner rows alias
        // base's uninitialised inner pointers — which read nondet.
        assert(clone.get(0, 0) == a);
    }
}
