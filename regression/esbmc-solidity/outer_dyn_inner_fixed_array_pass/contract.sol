// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Round-trip test for `uint256[N][]` (outer dynamic, inner fixed).
// Under correct semantics the writes round-trip.
//
// Currently KNOWNBUG: SMT encoding coredumps during
// `Encoding remaining VCC(s) using bit-vector/floating-point arithmetic`.
// This is an independent bug from the `uint[N][M]` value-set issue —
// symex completes fine, the SMT encoder aborts on the mixed
// pointer-backed-outer + fixed-inner shape.
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
