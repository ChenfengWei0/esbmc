// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// KNOWNBUG: struct containing a fixed-size array.  Post-clone equality
// fails in the current frontend because `*c = *base` at the GOTO level
// doesn't propagate the nested array's backing-store contents — the
// inner uint256[2] `cells` field ends up as a dangling or nondet slot
// in the clone struct.  Works for top-level `uint256[N]` (passes in
// esol_clone_fixed_array_pass) because that form's per-instance pool
// setup is done during `new C()`, but nested-struct-array init isn't
// reached.  Fix requires extending build_tod_clone_helper to emit an
// explicit per-nested-array element copy after the whole-struct
// assignment, OR fixing the ctor to pre-initialise nested-struct
// arrays.  Tracked here until either fix lands.
function __ESOL_shallow_copy(C src) pure returns (C) { return src; }

contract C {
    struct Box { uint256[2] cells; }
    Box private bx;
    function setCells(uint256 a, uint256 b) public {
        bx.cells[0] = a; bx.cells[1] = b;
    }
    function cell(uint256 i) public view returns (uint256) { return bx.cells[i]; }
}

contract H {
    function check(uint256 a, uint256 b) public {
        C base = new C();
        base.setCells(a, b);
        C clone = __ESOL_shallow_copy(base);
        assert(clone.cell(0) == a);
        assert(clone.cell(1) == b);
    }
}
