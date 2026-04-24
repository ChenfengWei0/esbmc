// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Round-trip test for `uint256[N][]` (outer dynamic, inner fixed).
// Under correct semantics the writes round-trip.
//
// KNOWNBUG (partial — single-write cases fixed 2026-04-24): the
// smt_conv select-decompose asymmetry patch now correctly routes 2D
// reads whose outermost array is infinite through a direct two-step
// SMT select, instead of synthesising a flat `i*N+j` key that was
// being applied against the outer infinite domain. The sibling
// `_fail` (1 write + 1 violated assert) runs to VERIFICATION FAILED
// in under a second.
//
// This variant (6 writes + 3 asserts) still trips
// `array_convt::unbounded_array_ite` during VCC encoding: cvc5 crashes
// on a shared_ptr deref, bitwuzla on `mk_ite`. The crash sits in the
// array_convt internal bookkeeping of multiple updates to the same
// infinite array id — execute_array_trans receives a `false_vals`
// vector with NULL slots — not in the index-lowering logic. Fixing it
// requires teaching array_convt to extend older per-update valuation
// vectors when new index keys appear later, which is out of scope for
// the frontend-level fix.
// See docs/claude/solidity/language-support.md §B.
contract OuterDynInnerFixedPass {
    uint256[3][] public grid;

    function check() external {
        grid.push();
        grid[0][0] = 10;
        grid[0][1] = 20;
        grid[0][2] = 30;
        assert(grid[0][0] == 10);
        assert(grid[0][1] == 20);
        assert(grid[0][2] == 30);
    }
}
