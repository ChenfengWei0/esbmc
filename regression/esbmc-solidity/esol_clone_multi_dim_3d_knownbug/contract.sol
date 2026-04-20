// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
// 3D fixed-array `uint256[M][N][K]` through `__ESOL_deep_copy`.
// Currently KNOWNBUG — the 2D workaround `_ESBMC_arrcpy_2d` collapses
// only the innermost two layers into one C-frame helper call, so the 3D
// walker still emits the (A) outer-alloc + (B) per-slot write pattern
// at the outermost layer, which hits the same value-set precision loss
// the 2D workaround was papering over for 2D.
//
// The value-set for `dynamic_object<clone>.arr` ends up with an extra
// `*` (may-point-to-any-object) entry, so `base->arr[0]` reads as NULL
// on some path — firing `_ESBMC_element_null_check` inside the per-slot
// `_ESBMC_arrcpy_2d(base->arr[i], ...)` call.
//
// raw C / raw C++ equivalents (see regression/esbmc-solidity/esol_clone_multi_dim_pass/
// repro_raw/raw_u256_c_3d.c and raw_u256_cpp_3d_layout_mirror.cpp) mirror
// the exact GOTO sequence and the exact contract struct layout and still
// VERIFY SUCCESSFUL — so the bug is in the Solidity frontend emission /
// value-set-analysis interaction, not in the backend's handling of the
// GOTO shape.
//
// See regression/esbmc-solidity/esol_clone_multi_dim_pass/INVESTIGATION.md
// for the full trace (Phase A pure-C reproduction, Phase B value-set
// diff, disproved nil→nondet hypothesis).
//
// Must flip KNOWNBUG → CORE once the root-cause fix in symex / value-set
// lands. Do NOT ship an `_ESBMC_arrcpy_nd` library helper as "the fix":
// generalising the 2D workaround to N-D is explicitly rejected by the
// user ("不要做任何compromise").
function __ESOL_deep_copy(C src) pure returns (C) { return src; }

contract C {
    uint256[2][2][3] arr;
    function setAt(uint256 i, uint256 j, uint256 k, uint256 v) public { arr[i][j][k] = v; }
    function get(uint256 i, uint256 j, uint256 k) public view returns (uint256) { return arr[i][j][k]; }
}

contract H {
    function check(uint256 a) public {
        require(a != 0);
        C base = new C();
        base.setAt(0, 0, 0, a);
        C clone = __ESOL_deep_copy(base);
        // Round-trip: clone should see base's write.
        assert(clone.get(0, 0, 0) == a);
    }
}
