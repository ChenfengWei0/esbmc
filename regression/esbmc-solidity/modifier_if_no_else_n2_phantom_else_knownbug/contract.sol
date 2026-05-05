// SPDX-License-Identifier: MIT
// KNOWNBUG: `if (cond) _;` modifier (NO else) + 2-statement body silently
// miscompiles. The splice walker erases the placeholder from op1() of the
// 2-op `ifthenelse(cond, _;)` and inserts N=2 body statements as siblings.
// Result: 3-op `ifthenelse(cond, stmt1, stmt2)`, which goto_convert
// interprets as a 3-op WITH-ELSE form: `if (cond) stmt1; else stmt2;`.
//
// The bug PROMOTES the second body statement into a phantom else-branch
// that runs for non-owner callers — exactly opposite of the modifier's
// intent (no-else means "do nothing if !cond", not "do stmt2 if !cond").
//
// Detector: `setBoth()` writes both `x = 1` and `y = 2`. Under correct
// semantics, owner runs both → state (1, 2); non-owner runs neither →
// state (0, 0). Under bug, owner runs only `x = 1` → (1, 0); non-owner
// runs only `y = 2` → (0, 2). Probe asserts the invariant
// `(x == 0 && y == 0) || (x == 1 && y == 2)` which holds under fix
// but fails for either bug-affected caller path.
//
// Pre-fix output: VERIFICATION FAILED (the assertion fails for owner or
// non-owner — both produce desynced state).
// Post-fix output: VERIFICATION SUCCESSFUL.
//
// KNOWNBUG mode + regex `^VERIFICATION SUCCESSFUL$` silently passes
// today (regex does NOT match the FAILED output) and will flag for
// promotion to CORE once the splice fix lands.
//
// (User chose this n=2 phantom-else shape over the with-else+empty-body
// case because the latter is observationally invisible to ESBMC — both
// bug and fix leave state unchanged for empty-body functions.)
pragma solidity >=0.8.0;

contract C {
    address owner;
    uint256 x;
    uint256 y;

    constructor() {
        owner = msg.sender;
    }

    modifier g {
        if (msg.sender == owner) _;
    }

    function setBoth() external g {
        x = 1;
        y = 2;
    }

    function probe() external view {
        // Invariant: x and y are written together (or neither).
        // Under correct semantics this holds for all owner/non-owner
        // call sequences. Under the splice phantom-else bug, owner-only
        // and non-owner-only call paths produce desynced state.
        assert((x == 0 && y == 0) || (x == 1 && y == 2));
    }
}
