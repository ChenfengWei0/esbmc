// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// Documents current shallow_copy semantics for fixed-size arrays:
// they are POINTER-COPIED.  ESBMC's Solidity model stores uint256[N]
// at a contract-level inf-size pool and keeps a pointer to it inside
// the contract struct.  `*c = *base` in build_tod_clone_helper copies
// that pointer, so post-clone writes on base ARE VISIBLE via clone —
// there is no per-instance backing buffer for fixed arrays.
//
// This is a known limitation of shallow_copy.  The test asserts the
// aliasing outcome so that a future true deep-copy fix flips this to
// VERIFICATION FAILED (at which point the companion
// esol_clone_fixed_array_deepcopy_* pair should be re-designed).
function __ESOL_shallow_copy(C src) pure returns (C) { return src; }

contract C {
    uint256[3] public arr;
    function setAt(uint256 i, uint256 v) public { arr[i] = v; }
    function get(uint256 i) public view returns (uint256) { return arr[i]; }
}

contract H {
    function check(uint256 a, uint256 b) public {
        C base = new C();
        base.setAt(1, a);
        C clone = __ESOL_shallow_copy(base);
        base.setAt(1, b); // mutate base AFTER clone
        // Aliasing property: clone sees b (the post-clone write on base).
        assert(clone.get(1) == b);
    }
}
