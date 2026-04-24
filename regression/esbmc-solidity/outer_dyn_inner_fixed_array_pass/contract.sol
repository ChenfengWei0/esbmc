// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Round-trip test for `uint256[N][]` (outer dynamic, inner fixed).
// Under correct semantics the writes round-trip.
//
// KNOWNBUG (partial — single-writes-that-hit-the-written-slot fixed
// 2026-04-24): the smt_conv select-decompose asymmetry patch now
// correctly routes 2D reads whose outermost array is infinite through
// a direct two-step SMT select. The sibling `_fail` (1 write + 1
// violated assert on the written slot) runs to VERIFICATION FAILED
// in under a second.
//
// Any read of an UNWRITTEN slot (e.g. `grid[0][1]` after only writing
// `grid[0][0]`) still crashes in `cvc5_convt::mk_ite` from inside
// `array_convt::execute_array_ite`. Root cause is a type-model
// inconsistency in the frontend: Solidity lowers inner fixed-size
// arrays as `pointer<T>` with `#sol_array_size` / SolType::ARRAY, then
// promotes `T[N][]` state vars to `array<pointer<T>, inf>` (see
// `solidity_convert_decl.cpp` is_dynarray_state block). The push
// emission writes an array-literal `{0, ..., 0}` of sort
// `array<T, N>` through a pointer-sorted slot. Inside array_convt the
// initial/fresh slot value has SMT sort STRUCT (fat pointer) while a
// post-write slot value has SMT sort ARRAY (the literal), and ITE
// between these distinct-sort valuations crashes the solver.
//
// Proper fixes (out of scope here):
//   (a) Unify the fixed-array model: make inner `T[N]` lower to
//       `array_typet(T, N)` throughout the Solidity frontend instead
//       of `pointer<T>` with size metadata. Touches most of
//       solidity_convert_type / solidity_convert_ref / the arrcpy
//       helpers.
//   (b) Coerce the write RHS to match `array_subtypes[arrid]` in
//       array_convt before recording. Avoids silent sort drift but
//       is a semantically-fragile patch.
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
