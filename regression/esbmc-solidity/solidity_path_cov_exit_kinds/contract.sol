// How a complete path EXITS is part of what a counterexample means: a
// reverting transaction discards its state changes, a normal one keeps them.
// This contract pins all three classifications at once.
//
//   h(0)  -- plain early `return`: a NORMAL exit
//   r(0)  -- failing `require`   : a REVERT exit
//
// The two are compiled to byte-identical goto (`IF <guard> THEN GOTO
// <END_FUNCTION>`, both skipping the function epilogue, neither carrying any
// marker), so they cannot be told apart structurally. The revert is recognised
// only by the frontend's `_ESBMC_sol_mark_revert()` call, which
// --solidity-path-coverage enables; the early return has no such evidence and
// is therefore reported `undetermined` rather than guessed to be normal —
// calling it normal would assert that a reverted transaction succeeded, and
// calling it revert would libel a successful one.
//
// Expected: normal 2 (h's fall-through + r's success), revert 1 (r's require
// failure), undetermined 1 (h's early return).
pragma solidity ^0.8.0;

contract T {
    uint256 public x;

    function h(uint256 a) public {
        if (a == 0)
            return;
        x = 1;
    }

    function r(uint256 a) public {
        require(a != 0);
        x = 2;
    }
}
