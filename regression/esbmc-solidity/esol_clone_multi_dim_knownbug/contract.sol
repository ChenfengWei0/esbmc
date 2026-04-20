// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
// KNOWNBUG: multi-dim fixed array `uint256[M][N]` post-clone read
// still fails AFTER Phase 2 + Phase 1.  Investigation log:
//
// What Phase 1+2 does correctly (confirmed in the goto dump):
//   - ctor emits `this->grid = alloc_array(3, 8)` (outer) followed by
//     `this->grid[i] = alloc_array(2, 32)` for i=0..2 (inner rows).
//   - base round-trip `base.setAt(0,0,a); base.get(0,0)==a` PASSES.
//   - clone helper emits `*c = *base` then `c->grid = alloc_array(3,
//     8)` (fresh outer) then `c->grid[i] = arrcpy(base->grid[i], 2,
//     32)` for each i.  All three arrcpy calls fire.
//
// What still fails:
//   `assert(clone.get(0,0) == a)` — the read resolves to something
//   that solver cannot pin to `a` even though the symbolic arrcpy
//   should have copied base's inner rows byte-for-byte.
//
// Raw-C/C++ repro of the EXACT same shape PASSES:
//   - `raw_u256_c.c`: MALLOC + memcpy struct-copy + alloc_array outer
//     + arrcpy inner — VERIFICATION SUCCESSFUL on bitwuzla.
//   - `raw_u256_cpp.cpp`: cpp_new + operator= (C++ auto-lowers struct
//     copy to a function call) + same arrcpy pattern — SUCCESSFUL.
//   Therefore this is NOT a pure backend (goto-symex/solver) bug —
//   the backend handles the pattern correctly in C/C++ mode.  The
//   bug is somewhere in the interaction between Solidity-frontend
//   emission and symex.  Specifically suspected: the `*new_ptr = tmp`
//   struct ASSIGN from `C base = new C()` (via cpp_new + get_new_
//   object_ctor_call) emits as DIRECT struct ASSIGN in Solidity mode,
//   whereas C++ mode lowers the same statement to `operator=(...)`
//   function call.  The function-call boundary appears to preserve
//   symex value-tracking that direct ASSIGN breaks.  Attempted fix
//   (route struct copy through memcpy in clone helper alone) did NOT
//   resolve the issue, suggesting the trigger is also the struct-
//   ASSIGN in `check()` for the initial `C base = new C()` or in
//   subsequent dispatch paths.
//
// Scope: tracked here until the root cause (and the right place to
// splice memcpy or equivalent) is pinned down with a smaller repro.
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
