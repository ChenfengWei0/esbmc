// The refutation must produce the NEXT BOX, not just a verdict.
//
// Same contract as the certify pair. Box [5,100] for the `a > 10` path is
// refuted with witness a = 10. The path's own counterexample lies above the
// witness, so the cut goes on the witness's side and lands ON it: retry with
// a in [11, 100]. That is exactly D_path intersected with the original box —
// ONE refutation, ONE exact cut, no bisection.
//
// This is the difference between a landing point and a search. Blind bisection
// on [5,100] would take several rounds to find 11 and would not know when to
// stop; the refutation already says where the boundary is not, so the shrink
// uses it. The withdrawn widening route failed on exactly this — it had no
// terminating condition and sat on the too-coarse-fails / too-fine-is-expensive
// dilemma.
//
// The confirmation half of this test is `solidity_path_cov_certify_box_inside`,
// which certifies [11,100] — the very box suggested here. So the pair is
// suggestion + confirmation, and neither half alone shows the loop closes: this
// one shows a cut is produced, that one shows the produced cut is correct.
//
// The suggestion is printed, never applied. The tool measures, the driver
// decides — the same line kept everywhere else (the ladder span comes from the
// driver too).
pragma solidity ^0.8.0;

contract Box {
    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
