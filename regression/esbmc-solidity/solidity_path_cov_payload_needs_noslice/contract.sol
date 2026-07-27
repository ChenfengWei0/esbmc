// Asking for the JSON report must actually produce a usable counterexample
// payload.
//
// A path claim's guard mentions nothing but the ghost path accumulators, so the
// symex slicer — which keeps only what the claim depends on — removes every
// contract-state write and every nondet input. The report then came back with
// empty `inputs` and empty `final_state`: an interface whose entire purpose is
// those values, silently delivering none of them.
//
// Requesting `--cov-report-json` therefore disables slicing, and says so on
// stdout rather than changing a flag behind the user's back. This test pins
// that message. Slicing is untouched when the report is not requested.
pragma solidity ^0.8.0;

contract D {
    uint256 public x;

    function g(uint256 a) public {
        require(a != 0);
        if (a > 100) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
