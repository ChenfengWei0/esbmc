// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// KNOWNBUG (non-coverage / plain verification pin).
//
// State-var nested mapping whose value is a struct =
// array^K<Struct{...}> with K>=2 and infinite outer levels.  The
// default node tuple flattener (bitwuzla) convert_sort's an
// array-of-array-of-struct and hits NW1 (smt_conv.cpp:2858 ->
// to_solver_smt_sort<> on a bare struct sort):
//
//   ESBMC internal error: bare smt_sort (id=4) reached to_solver_smt_sort<>
//
// This abort is NOT coverage-specific -- it fires under ordinary
// `--contract C` BMC, before solving.  The desired post-fix behaviour
// is `VERIFICATION SUCCESSFUL` (the write/read round-trip holds), so
// that is the KNOWNBUG regex: it does not match today (abort) and will
// match once Stage 2C lands, triggering the KNOWNBUG->CORE flip.
//
// Dual: mapping_struct_smtsort_k1_pass (K=1, identical flags) is CORE
// SUCCESSFUL today and is the regression guard for the v3 fix's
// K=1-byte-identical invariant.
contract C {
    struct S {
        uint256 a;
        uint256 b;
    }

    mapping(uint256 => mapping(uint256 => S)) m;

    function check(uint256 i, uint256 j, uint256 v) public {
        m[i][j].a = v;
        assert(m[i][j].a == v);
    }
}
