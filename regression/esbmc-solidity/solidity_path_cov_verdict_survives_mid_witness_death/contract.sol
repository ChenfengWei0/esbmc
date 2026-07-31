// A VERDICT THAT WAS MADE MUST SURVIVE THE RUN THAT DIED MAKING ITS TEST.
//
// One refutation is recorded TWICE, at different times:
//
//   claim_outcome[sig] = 'F'      the instant the solver answers SAT
//   reached_claims.emplace(sig)   only after build_goto_trace, the CE harvest
//                                 and every artifact emitter have run
//
// Everything in that window allocates. The report's numerator read only the
// second, so a run that died in the window had a verdict it printed and did not
// report.
//
// MEASURED, on notes/coverage/poc/P16_Mapping.sol -- a 30-line nested-mapping
// contract, 8 paths, std::bad_alloc at 4 GB:
//
//     ✗ FAILED: 'put:path:7 at'
//     Path Status: F 0, I 0, U 8
//     ERROR: --solidity-path-coverage: INTERNAL DEFECT — 1 path(s) are reported
//            U with NO reason token: sol:@C@P16_Mapping@F@put#31:path:7
//
// The invariant was RIGHT to fire and its cause was in the reporting: a
// witnessed path filed as undecided, which on a live run is indistinguishable
// from an honest "could not decide". `path_u_reason_token` has no token for
// 'F' -- correctly, an F is not a U -- so the contradiction surfaced as an
// abort rather than as a quietly wrong number, which is the only reason it was
// noticed at all.
//
// ---- WHY A NEW FAULT INJECTOR WAS NEEDED ----
//
// `--path-cov-fault-after N` cannot reach this state. It fires at the START of a
// job, by which point every earlier claim has completed ALL of its side effects
// including reached_claims. The sibling test
// `solidity_path_cov_partial_report_on_oom` uses it and pins the U-reason split;
// it cannot pin this, because under that injector the two records never
// disagree. `--path-cov-fault-mid-witness N` throws from inside the harvest of
// the Nth refuted claim, which is exactly the window.
//
// ---- WHAT IS PINNED, AND WHY BOTH LINES ----
//
//     Reached : 1        Path Coverage: 25%     Path Status: F 1, I 0, U 3
//
// `Reached` and `F` are two renderings of one set and were computed from two
// different tests. Fixing only the second produced a report reading
// `Path Coverage: 0%` beside `Path Status: F 1` -- measured, on this contract,
// on the way to this fix. Both are pinned so they cannot drift apart again: a
// reader who saw them disagree would have to guess which was the defect.
//
// The payload may be absent for such a claim -- the harvest is what died -- and
// that is the honest report: a witness exists and its values did not survive.
// Dropping the F instead asserts something false about the program.
//
// Same contract as the sibling OOM test on purpose: every path is feasible, so
// the only thing that differs between the two tests is WHERE the fault lands.
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
