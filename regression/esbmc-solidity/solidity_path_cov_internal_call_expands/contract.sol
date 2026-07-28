// The unit rule and the expansion rule are SEPARATE, and this contract pins
// both at once.
//
//   * a UNIT is a public/external function -- what an external caller can
//     invoke. `helper` is private, so it is NOT a unit and has no path set of
//     its own.
//   * an INTERNAL CALL expands into the caller, so the callee's decisions are
//     part of the caller's path identity.
//
// `pub` is on both sides of that split, which is the case that makes the two
// rules visibly independent: it is a unit (entered from outside, `a` free) AND
// it is expanded into `caller` (entered with `caller`'s argument). Both
// descriptions are needed; they describe different input spaces.
//
// Expected shape:
//   pub    -- 1 source decision -> 2 body paths, +1 ABI non-payable gate = 3
//   caller -- pub's decision AND helper's decision are both in its identity,
//             so 2 decisions -> 4 body paths, +1 gate = 5
//   helper -- not a unit: 0 paths of its own
//   total 8 across 2 units, with 1 in-scope function reported as a non-unit.
//
// One of `caller`'s five paths is INFEASIBLE, and that is the sharpest thing
// this test pins. `caller:path:14` takes `pub`'s `a <= 1` branch and `helper`'s
// `a > 3` branch — a contradiction that exists only because two DIFFERENT
// callees' decisions sit in ONE path identity. Reported F 7, U 1 (never I: no
// coverage configuration here establishes unreachability). Treat the callees as
// black boxes and this contradiction is not merely missed, it is unobservable:
// the two branches would live in separate path domains and the strongest
// assertion provable on `caller`'s single merged path would be the disjunction
// of all four combinations.
//
// If expansion ever regresses, `caller` collapses to 1 body path (+gate = 2),
// `helper` reappears as a third unit, and the infeasible combination vanishes
// — so the pinned "8 ... across 2 unit" line, the non-unit line and `U 1` are
// decisive in both directions.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function pub(uint256 a) public {
        if (a > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function helper(uint256 a) private {
        if (a > 3) {
            x = 3;
        }
    }

    function caller(uint256 a) public {
        pub(a);
        helper(a);
    }
}
