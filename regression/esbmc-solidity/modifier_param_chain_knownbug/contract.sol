// SPDX-License-Identifier: MIT
// KNOWNBUG: ESBMC's intermediate-modifier-call branch in
// solidity_convert_modifier.cpp:1352-1388 fails to forward the wrapped
// function's formal parameters to the next wrapper, even though the
// wrapper signature (built at lines 1077-1106) requires them.
// Trigger condition: `>=2 modifiers stacked AND >=1 formal parameter
// on the wrapped function`. The final-call branch at lines 1389-1431
// does forward the params correctly (loop at lines 1402-1422), but
// the intermediate branch only pushes [this, mod_args] — missing the
// wrapped-function params in the middle.
//
// User-facing symptom: ESBMC emits a sourceless violation
//   "Violated property: function call: not enough arguments"
//   "VERIFICATION FAILED / Bug found (k = 1)"
// at base case k=1, blocking detection of any real bug in the
// contract.
//
// Existing modifier_3 / modifier_4 tests use a no-parameter wrapped
// function `function func2() public check check2 { ... }`, so the
// missing parameter loop iterates zero times and the bug stays
// silent. This regression covers the gap.
//
// Post-fix expectation: the assertion `x == before + a` is a true
// invariant (since the body sets `x = x + a`), so VERIFICATION
// SUCCESSFUL is the correct verdict.
pragma solidity >=0.8.0;
contract C {
    uint256 public x;
    modifier m1() { _; }
    modifier m2() { _; }
    function f(uint256 a) public m1 m2 {
        uint256 before = x;
        x = x + a;
        assert(x == before + a);
    }
}
