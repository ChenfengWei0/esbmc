// A non-payable public function REVERTS when called with value, before a
// single statement of its body runs. The frontend does not model that check,
// which costs two different things: a missing path (every non-payable entry
// has a real, testable "called with value -> revert" execution) and WRONG
// counterexamples (msg.value is havoc'd per transaction and nothing
// constrained it to zero, so a reported path could carry a nonzero msg.value
// that on-chain reverts at entry -- a test that cannot replay).
//
// The check is synthesised as an ordinary ABI-layer decision, so it is
// enumerated like any other. Two functions with IDENTICAL bodies differing
// only in `payable` therefore differ by exactly one path:
//
//   np  (non-payable) -- 2 body paths + 1 value-reject path = 3
//   pay (payable)     -- 2 body paths                       = 2
//
// Before the gate existed the two enumerated identically (measured), which is
// what this test exists to prevent recurring.
pragma solidity ^0.8.0;

contract P {
    uint256 public x;

    function np(uint256 a) public {
        if (a > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function pay(uint256 a) public payable {
        if (a > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
