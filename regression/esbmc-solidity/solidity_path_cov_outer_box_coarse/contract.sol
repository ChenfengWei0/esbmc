// Stage-2 OUTER BOX, coarse round: the ladder resolution is not good enough to
// separate the two paths, and the tool says so instead of shipping the overlap.
//
// `f` has two paths: enc=2 (`a > 10`, true domain [11, 2^256-1]) and enc=3
// (fall-through, true domain [0, 10]). The ladder spans [0,100] with 20 probes,
// i.e. resolution 100/21 ~ 4.76, so the measured outer boxes are
//   enc=2  a in [9,  2^256-1]     (9 <= 11: a valid OUTER bound, just loose)
//   enc=3  a in [0,  14]          (14 >= 10: likewise)
// Both are correct — an outer box only has to CONTAIN the domain — and both are
// too loose to be told apart, because they overlap on [9,14].
//
// What the subtraction then does is the point of this test:
//   enc=2 keeps [15, 2^256-1]: cutting above enc=3's box is legal, since enc=2's
//         counterexample is far above 14.
//   enc=3 CANNOT be separated: the only cut that would exclude enc=2's box keeps
//         a < 9, and enc=3's counterexample is a = 10, so the cut would carve
//         away a KNOWN member of its own domain. The tool reports the sibling as
//         unseparated and says the region still overlaps.
//
// That warning is the whole test. Silently keeping [0,14] would hand downstream
// a region containing inputs (11..14) that provably walk the other path — a
// certified-looking test that is red on the unmodified contract, which is the
// one outcome this pipeline must never produce. The `_refined` twin shows the
// same measurement at a resolution that does separate them; together they show
// the loop is a refinement loop, not a one-shot.
pragma solidity ^0.8.0;

contract Box {
    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
