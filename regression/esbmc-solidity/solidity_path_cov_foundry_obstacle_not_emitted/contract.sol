// A NAMED OBSTACLE MUST NOT BECOME A TEST — the emission half of the rule.
//
// goto_coverage.h states it outright for named_obstacle_paths: "a marked path
// ... must not be turned into a test. Marking without excluding would be
// worthless." Only the marking half was implemented. This test pins the other
// half, and it exists because the gap was invisible from every number the tool
// already printed.
//
// WHY IT WAS INVISIBLE. `named_obstacle_paths` had exactly three readers, and
// all three sit under `if (tri == "U")` in bmc.cpp — the report's `u_reason`
// and `u_reason_detail`. A REFUTED path is 'F', never 'U', so it reached none
// of them. Refuted is also the only kind that ever becomes a test. The Foundry
// generator did not mention the map at all. So a path could be marked, counted
// in the NAMED OBSTACLE warning as excluded, and shipped as a test in the same
// run.
//
// THE TWO NUMBERS ON ONE RUN ARE THE POINT, and they are pinned together:
//
//     U Reasons: named-obstacle 1, ...       <- what the report showed
//     Foundry: 4 counterexample(s) REFUSED   <- what was actually excluded
//
// Unit `a` has 5 paths and ALL of them are obstacles (its call to the public
// `c` stays unexpanded at --unwind 1, so an INTERNAL call is routed through the
// EXTERNAL-entry body and its ABI value gate — an execution that does not exist
// on chain). Of those 5, four are refuted (a:path:2, 12, 13, 15) and one holds
// at this bound (a:path:14). The report's "named-obstacle 1" is that single
// holding path; the four refuted ones produced four counterexamples, and before
// this each became an emitted test case. A test replaying one is RED on the
// UNMODIFIED contract, which is the single outcome this pipeline must never
// produce.
//
// WHERE THE CHECK LIVES, and why not on the segment. The generator has TWO
// reconstruction routes: the dispatcher-segment route, and a coverage-claim
// fallback used when no segment got a method. A flag attached to the segment
// would be correct on the first and silently zero on the second — a detector
// that reports a confident zero on half its inputs. The refusal is therefore
// keyed off the REFUTED CLAIM (the obligation the case is reconstructed from)
// using the same (comment, location) pair the census stores, so it is
// route-independent and no string has to round-trip for it to fire.
//
// THE PAIRING — three tests over one contract:
//   solidity_path_cov_residual_unit_call_obstacle    marking side,  --unwind 1
//   solidity_path_cov_residual_unit_call_expanded    no obstacle,   --unwind 3
//   this one                                         emission side, --unwind 1
// If the containment stops firing, the refused count drops to 0 and this test
// goes red while the marking test stays green — which is exactly the split that
// was missing, and why the marking test alone could not have caught this.
//
// `Generated ... 6 case(s)` is pinned alongside the refusal count on purpose: a
// change that stops refusing raises it, a change that over-refuses lowers it, so
// the two cannot both stay right by accident.
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
