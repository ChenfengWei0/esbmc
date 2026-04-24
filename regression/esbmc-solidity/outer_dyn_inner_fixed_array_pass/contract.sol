// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Round-trip test for `uint256[N][]` (outer dynamic, inner fixed).
// Under correct semantics the writes round-trip.
//
// KNOWNBUG (architectural, tracked): the Solidity frontend lowers this
// to `array<array<uint256, 3>, inf>` (outer unbounded, inner native
// fixed). ESBMC's flat-array encoder (`array_convt`) asserts at
// `src/solvers/smt/array_conv.cpp:92-95` that it cannot represent
// unbounded arrays whose element sort is itself an array sort. In
// release builds the assert is compiled out and the encoder crashes
// later (segfault in bitwuzla's `mk_ite`, cvc5 shared-pointer bad addr).
// Symex completes fine — the abort is purely at SMT-conversion time.
//
// Same root cause as `mapping_fixed_array_unbound_*`. Real fixes that
// are on the table (none in scope here):
//   (a) smt_conv: flatten nested inf arrays into one domain (~1-2 wk,
//       cross-frontend regression sweep).
//   (b) frontend: lower `T[N][]` as flat `T[]` with stride N, rewrite
//       `a[i][j]` → `a[i*N+j]`. Touches index lowering across files.
//   (c) skip array_convt for this pattern and let the solver's native
//       array theory handle both dims (bitwuzla/cvc5 support).
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
