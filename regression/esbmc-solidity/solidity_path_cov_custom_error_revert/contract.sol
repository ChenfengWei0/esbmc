// A custom-error revert (`revert E()`) is an ORDINARY complete path and must
// be coverable. Two properties are locked here:
//
//  1. Reached : 2 — the reverting path yields a counterexample. It previously
//     did not: `revert E()` lowered to a callee containing only
//     ASSUME(false), which pruned the path before it could reach the exit
//     assertion, so the path was silently reported as proven-unreachable.
//  2. Complete Paths : 2 — the lowered error function `E` is NOT itself
//     enumerated as a unit under test. It is the lowering of a statement, and
//     its single degenerate path is uncoverable by construction, so counting
//     it would permanently deflate coverage (2/3 instead of 2/2).
pragma solidity ^0.8.0;

contract N {
    error TooSmall();

    uint256 public x;

    function f(uint256 a) public {
        if (a < 5)
            revert TooSmall();
        x = a;
    }
}
