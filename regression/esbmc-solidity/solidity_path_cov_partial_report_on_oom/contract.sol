// A RUN THAT DIES MUST KEEP WHAT IT DECIDED, AND MUST SAY THAT IT DIED.
//
// MEASURED: the aqua whole-contract run at --memlimit 8g died 51.5% through the
// solve having DECIDED 938 claims and REFUTED 5 of that contract's 15 complete
// paths, and reported none of it. report_coverage sits AFTER the per-claim job
// loop and INSIDE run_thread's try (bmc.cpp:3661 vs :2405), and the only
// verification-phase catch is at :2559 -- so an allocation failure in any job
// unwound straight past the report. A CAUGHT OOM cost the ENTIRE report, not
// part of it.
//
// The loop is now wrapped: the tail runs on the exception path too and then
// rethrows, so nothing downstream sees a different control flow. Deliberately
// NOT a per-job catch that swallows the failure and carries on -- when memory
// is genuinely exhausted the next job throws as well, and a loop that absorbs N
// allocation failures files N claims as "solver-unknown" while spending the rest
// of the budget failing.
//
// WHAT THIS PINS, and each line is a separate failure it would otherwise let
// through:
//
//   1. `Report Completeness: PARTIAL`, naming std::bad_alloc. The partial
//      report is written to the SAME cov-report.json a complete one is -- there
//      is nowhere else a consumer would look, and putting it elsewhere
//      reproduces the old behaviour of keeping nothing. So this marker is the
//      ONLY thing separating them. A partial report read as complete would
//      deflate every numerator in this project, silently.
//
//   2. `Coverage report written to cov-report.json`. Without it the test would
//      pass on a build that prints an honest PARTIAL banner and still writes
//      nothing -- a nicer-looking version of exactly the defect.
//
//   3. The U-REASON SPLIT, pinned as a whole line WITH the zeros in it:
//      `not-solved-this-run 0, run-died-before-solving N`. Those two used to be
//      one bucket. `not-solved-this-run` means the simplifier folded the claim
//      to true at symex time -- a property OF THE CLAIM, identical on every
//      re-run, which no budget changes. `run-died-before-solving` means the
//      process stopped issuing jobs -- a property OF THE RUN, for which
//      re-running with a bigger budget is the fix. Collapsed, a partial run
//      shows a large `not-solved-this-run` count that reads as the simplifier's
//      doing. The ZERO on the old token is asserted, not just the non-zero on
//      the new one: a build that fired both would still look right if only the
//      new one were checked.
//
// The fault injector is a shipped option rather than a throwaway build, because
// a test.desc is one invocation with no environment of its own and the harness
// strips --timeout and --memlimit (testing_tool.py UNSUPPORTED_OPTIONS).
//
// Every path of this contract is feasible, so a fault after claim 1 leaves
// exactly the shape the aqua run had: some claims decided and witnessed, the
// rest never reached.
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
