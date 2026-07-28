// Stage-2 OUTER BOX, refined round: a narrower ladder span separates the paths
// exactly, and the subtraction then costs no query at all.
//
// Same contract as the `_coarse` twin. The span is [5,20] with 14 probes, i.e.
// resolution 1, so the measured outer boxes are exactly the domains:
//   enc=2  a in [11, 2^256-1]
//   enc=3  a in [0,  10]
// They no longer overlap, so no cut is needed, no sibling is left unseparated,
// and the certified regions equal the outer boxes.
//
// The pair coarse/refined is the loop: the tool measures, reports honestly what
// it could not separate, and the driver comes back with a narrower span. The
// refinement is the DRIVER's move — a non-adaptive batch of K probes gives
// resolution (hi-lo)/(K+1), never log(hi-lo), and pretending otherwise is how a
// "one batch is enough" claim would quietly become false on a 256-bit input.
//
// The regions here were subsequently put through --path-cov-certify: [0,10] for
// enc=3 comes back SUCCESSFUL, and [0,11] — one wider — comes back FAILED at the
// OTHER path's exit. So the subtraction's output is confirmed by the independent
// query rather than trusted.
//
// Pinned with a full-text negative lookahead on the unseparated-sibling warning:
// without it, a regression in the cut logic that stopped separating anything
// would still print two plausible regions and pass on the numbers alone.
pragma solidity ^0.8.0;

contract Box {
    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
