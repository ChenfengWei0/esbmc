// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Violation test for `uint256[N][]` (outer dynamic, inner fixed).
// Under correct semantics the `grid[0][0] != 10` assertion is violated.
//
// Currently KNOWNBUG: SMT encoding coredumps before reaching the solver.
// Independent of the `uint[N][M]` value-set issue.
// See docs/claude/solidity/language-support.md §B.
contract OuterDynInnerFixedFail {
    uint256[3][] public grid;

    function check() external {
        grid.push();
        grid[0][0] = 10;
        assert(grid[0][0] != 10);
    }
}
