// THE PUNCHED REGION ACTUALLY CERTIFIES, and it is the FIRST HALF of a
// must-flip pair with solidity_path_cov_certify_hole_missing_fail: the two
// specs differ by one JSON key.
//
//     box `to in [0, 2^160-1] \ {255}`  ->  VERIFICATION SUCCESSFUL  (here)
//     box `to in [0, 2^160-1]`          ->  VERIFICATION FAILED      (sibling)
//
// Without the pair, this directory alone would be satisfied by a query that
// ignored the holes entirely and answered SUCCESSFUL for some other reason --
// which is exactly the shape of the two false-certification routes already
// found in this stage (an inverted interval, and a signed wrap). The sibling
// proves the box IS otherwise refutable, so the SUCCESSFUL verdict here can
// only come from the hole.
//
// WHY THIS PARTICULAR REGION. It is the one the sibling-subtraction now
// produces for this contract (see solidity_path_cov_punched_ce_independent):
// the whole address type minus the single banned value. Certifying it closes
// the loop -- the region the zero-query subtraction computes is a region the
// verifier confirms, rather than a candidate that still has to be shrunk.
//
// It is also the yield. The previous closed-interval regions for this path were
// 255 values or ~1.46e48 depending on which counterexample the solver returned;
// this one is 2^160-1 values and does not depend on the counterexample at all.
//
// FAULT INJECTION, run: skipping the `c != h` conjunction in the assumption
// flips this directory to FAILED and moves the reported hole count from 1 to 0,
// while the sibling stays FAILED and the empty-box refusal stays refused.
pragma solidity ^0.8.0;

contract Gate2 {
    uint256 public sink;
    address constant BANNED = address(0x00000000000000000000000000000000000000ff);

    function send(address to) external payable returns (uint256) {
        require(to != BANNED);
        sink = 1;
        return 1;
    }
}
