// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ISOLATES: internal-call inlining and what it does to PATH IDENTITY.
//
// `outer` calls `inner`, and `inner` has its own branch. The pass physically
// inlines the callee before enumerating, so `inner`'s decision becomes part of
// `outer`'s path identity: `outer` enumerates 2 x 2 = 4 complete paths, not 2.
//
// THE FIRST VERSION OF THIS FILE WAS WRONG AND THE RUN SAID SO. It passed the
// SAME `x` to both the outer guard and `inner`, so the two decisions were
// correlated: `x <= 5` implies `x <= 50`, making the combination `(x <= 5,
// inner takes its true arm)` impossible. Measured: 5 paths, 4 F, 1
// bounded-holds. The contract was quietly testing FEASIBILITY as well as
// expansion, and a reader would have blamed the expansion.
//
// `inner` now takes its own parameter, so the four combinations are genuinely
// independent and all four must be witnessed.
//
// EXPECTED: `instrumented 5 complete path(s) across 1 unit(s)` — 4 enumerated
// combinations plus the synthesised non-payable ABI gate, which contributes one
// reverting path to every unit — with F = 5 and bounded-holds = 0. Every path's
// decision sequence contains BOTH the outer and the inner decision.
//
// WHY THIS ONE IS LOAD-BEARING: internal-call expansion multiplied aqua's path
// count by 72.97x and farming's by 381.44x. That multiplication is the single
// largest driver of how many claims a run must solve, and it had never been
// observed on a contract where the right answer can be counted by hand.
//
// The double-identity variant, where the callee is PUBLIC and therefore also a
// unit in its own right, is P12.
contract P11_Inner {
    uint256 public tag;

    function inner(uint256 y) internal pure returns (uint256) {
        if (y > 50) {
            return 10;
        }
        return 20;
    }

    function outer(uint256 x, uint256 y) external {
        if (x > 5) {
            tag = inner(y);
        } else {
            tag = inner(y) + 1;
        }
    }
}
