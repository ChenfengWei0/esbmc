// Isolates, in one contract, the failure mode that made every value-returning
// unit's body paths uncoverable.
//
// `noRet` and `withRet` have the SAME decisions and the same body. They differ
// only in whether the function returns a value. A value-returning function ends
// in a RETURN instruction, and RETURN terminates the frame: symex does not fall
// through to END_FUNCTION. While the path identity asserts were placed at
// END_FUNCTION they were downstream of the frame exit and could never execute.
//
// Measured before the fix on this contract: `Reached : 4` of 6 — `noRet` fully
// covered, `withRet` covered only on its ABI value-reject path, which is the
// one path that reaches END_FUNCTION by a plain GOTO instead of through the
// RETURN. Not a crash, not a warning: coverage simply read low, and the missing
// paths were reported U ("could not decide"), which is indistinguishable from
// an honest solver timeout.
//
// This was invisible in every earlier test because none of them returned a
// value — while in real contracts getters and view functions all do.
//
// Expected now: 6 paths across 2 units, all reached. A regression drops
// `Reached` to 4.
pragma solidity ^0.8.0;

contract R {
    uint256 public x;

    function noRet(uint256 a) public {
        if (a > 3) {
            x = 1;
        } else {
            x = 0;
        }
    }

    function withRet(uint256 a) public returns (uint256) {
        if (a > 3) {
            x = 1;
        } else {
            x = 0;
        }
        return x;
    }
}
