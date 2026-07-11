// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

// A tuple function mixing an early explicit `return (x,y)` in one BRACE-LESS
// branch with a named-return fall-through in another. This used to be a known
// bug: the tuple-return binding statements queued while lowering the brace-less
// `if(c) return(1,2);` body leaked to the ENCLOSING block (placed after the
// `if`, unconditionally), so both paths collapsed to (1,2) and the fall-through
// (c==false) never got (3,4) — a spurious violation. flush_pending_into_body
// now keeps those statements inside the branch scope, so f(true)=(1,2) and
// f(false)=(3,4) both hold and the asserts verify. Regression pin for the
// brace-less if-body path-condition fix (see also the local-array OOB tests).
contract H {
    function f(bool c) public pure returns (uint a, uint b) {
        if (c) return (1, 2);
        a = 3;
        b = 4;
    }
    function check(bool c) public {
        (uint ra, uint rb) = f(c);
        if (c) {
            assert(ra == 1 && rb == 2);
        } else {
            assert(ra == 3 && rb == 4);
        }
    }
}
