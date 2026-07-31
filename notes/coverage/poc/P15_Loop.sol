// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: a loop whose trip count is an INPUT, against the unwind bound.
///
/// Path coverage installs `--unwind 4` for itself and then forces
/// `no-unwinding-assertions`, which turns the unwinding assert into an ASSUME
/// that SILENTLY PRUNES executions beyond the bound. So `n > 4` is not reported
/// as unreachable; it is assumed away.
///
/// EXPECTED: paths for `n` up to the bound are witnessed; anything requiring
/// more is reported as `bounded-holds` — and the run prints the under-report
/// warning naming the truncated loop.
///
/// WHY IT IS IN THE SET: this exact interaction was measured producing a WRONG
/// ANSWER on a real contract. With `--no-simplify`, a library loop stopped
/// being folded, was truncated at 4, and the assumption deleted the executions
/// that witnessed the path — F went 2 to 0 with exit 0, a normal report, and no
/// warning beyond the generic one. The verdict became `bounded-holds`: the tool
/// stating that the path does not hold, when it does.
///
/// Here the loop is mine, its trip count is the parameter, and the boundary is
/// at 4. Whatever the tool says about `n = 5` is checkable by reading the
/// source, which is the whole point of the PoC set.
contract P15_Loop {
    uint256 public acc;

    function sum(uint256 n) external {
        require(n <= 8);
        uint256 s = 0;
        for (uint256 i = 0; i < n; i++) {
            s += i;
        }
        if (s > 6) {
            acc = 1;
        } else {
            acc = 2;
        }
    }
}
