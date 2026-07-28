// The clean half of the pair. Same contract as
// solidity_path_cov_residual_unit_call_obstacle, run with a call depth bound
// deep enough (`--unwind 3`) to expand the whole a -> b -> c chain.
//
// This test exists because its partner is a DETECTOR: it claims "no unit calls
// another unit's own gated body unexpanded". A detector that can no longer hit
// anything is green forever and indistinguishable from a working one, which is
// the failure pattern this pipeline has already been bitten by. So the two are
// pinned together and each is decisive against the other's failure mode:
//
//   * partner (`--unwind 1`): the containment MUST fire — 13 paths, obstacles
//     (a) 0 / (b) 5 across 1 unit. If it stops firing, that test goes green with
//     zero obstacles and the empty-check has arrived.
//   * this one (`--unwind 3`): the containment MUST NOT fire — 17 paths, and a
//     whole-output negative lookahead pins that no NAMED OBSTACLE line is printed
//     at all. If the containment ever over-fires (e.g. keyed on "callee is a
//     unit" while ignoring whether the call was actually left unexpanded), this
//     goes red.
//
// The path counts are the second, independent pin: expanding one more level
// takes `a` from 5 to 9 (its identity gains `c`'s decision) and the total from
// 13 to 17. Numbers that move for a reason are harder to fake than a message.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function a(uint256 v) public {
        if (v > 1) {
            x = 1;
        }
        b(v);
    }

    function b(uint256 v) public {
        if (v > 2) {
            x = 2;
        }
        c(v);
    }

    function c(uint256 v) public {
        if (v > 3) {
            x = 3;
        }
    }
}
