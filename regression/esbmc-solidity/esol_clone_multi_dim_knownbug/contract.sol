// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
// KNOWNBUG: multi-dim fixed array `uint256[M][N]` post-clone read
// still fails on the CLONE side even after Phase 2 ctor-side init
// lands.  Status after Phase 2:
//   - `new C()` now recursively calloc's both outer AND inner rows
//     (Phase 2 walker emits `this->grid[i] = calloc(M, 32)` for each
//     outer slot), so SAME-instance `base.setAt(0,0,a); base.get(0,0)
//     == a` passes (esol_clone_multi_dim_base_pass, if present).
//   - Phase 1 clone walker correctly unrolls the per-slot
//     `_ESBMC_arrcpy(base->grid[i], M, 32)` and assigns to
//     `clone->grid[i]`.
//   - But the final read `clone.get(0,0) == a` still reads nondet/0
//     under symex.  Suspected root cause is in ESBMC's heap/pointer
//     model for nested pointer-of-pointer state fields after the
//     `*clone_c = *base` bit-copy followed by per-slot arrcpy
//     overwrites; the 1D case (esol_clone_fixed_array_isolation_pass)
//     works fine, so the regression is specific to inner-row arrcpy
//     visibility across the fresh outer allocation.
//
// Scope note: this is NOT a ctor-layer bug anymore — Phase 2 closed
// that side.  It is a symex / heap-model follow-up tracked here until
// the root cause in goto-symex (or in _ESBMC_arrcpy's element-copy
// routing) is nailed down.
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
