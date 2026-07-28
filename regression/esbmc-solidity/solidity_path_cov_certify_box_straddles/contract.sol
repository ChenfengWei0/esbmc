// Stage-2 CERTIFICATION QUERY — direction two: a box STRADDLING the boundary
// must be refuted, AND the refutation must carry the witness value.
//
// Same contract as the `_inside` twin (see its header for the path numbering
// and for why the assert goes on every exit). Here the box is [5,100], which
// contains inputs on both sides of `a > 10`.
//
// Two things are pinned, and the second matters as much as the first:
//
//   * the refutation lands on exit1 — the OTHER path's exit. exit0 PASSES here
//     exactly as it does in the `_inside` run, so an implementation that
//     asserted only on this path's own exit would report SUCCESSFUL for a box
//     that plainly is not certified. That is the vacuous-query failure this
//     placement exists to prevent, and this line is what detects it.
//   * the counterexample carries `a = 10` and `path_tr$0 = 3`. A verdict alone
//     is not enough: the refuting INPUT is what the box gets shrunk with, so a
//     refutation that cannot name it leaves the loop with nothing to do. This
//     was measured going wrong once — decorating the claim comment with a
//     `certify:` prefix broke the report's scope test, every nondet was filed
//     as harness-internal, and the witness silently vanished while the verdict
//     still printed. `path_tr$0 = 3` additionally shows the escaping input's
//     ACTUAL path number, which is the fact the query is asserting about.
pragma solidity ^0.8.0;

contract Box {
    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
