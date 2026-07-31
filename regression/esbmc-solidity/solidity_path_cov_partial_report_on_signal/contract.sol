// AN EXTERNALLY KILLED PATH-COVERAGE RUN USED TO EMIT NOTHING AT ALL.
//
// 27 runs across the corpus were killed at a 180 s outer timeout. Every one
// contributed exactly zero, and that zero is indistinguishable in the gate
// table from a measured zero -- st1inch's entire row (22 of 22 runs killed) is
// made of it.
//
// The cause is a single gate. `emit_branch_coverage_on_timeout`
// (esbmc_parseoptions.cpp:132) returns at its first line unless
// `goto_coveraget::branch_cov_active` is set, and that atomic has exactly ONE
// runtime writer in the whole tree: branch_coverage() at goto_coverage.cpp:2325.
// solidity_path_coverage() wrote none of the five signal-safe atomics, so on a
// path-coverage run the handler was a no-op for the entire process -- for
// SIGALRM (--timeout), SIGTERM (timeout(1), CI, the regression harness itself)
// and SIGINT alike.
//
// A SECOND ARM, NOT A WIDENED CONDITION. The branch metric's numerator and
// denominator are meaningless here: `total_branch` counts decision edges and
// `total_paths_atomic` counts complete paths, so printing one under the other's
// heading would be a wrong number wearing the right label.
//
// WHAT THIS PINS:
//
//   1. `Report Completeness: PARTIAL . terminated by signal`. Same discipline as
//      the OOM arm: the completeness of a coverage block is a fact that has to
//      be stated, never inferred from the block existing.
//
//   2. `Claims Decided : 1 of 4` -- the numerator AND the denominator. A bare
//      "1" would be compatible with a run that had one claim; the pair is what
//      says how much was lost.
//
//   3. The disclaimer TEXT, verbatim, including "no cov-report.json was
//      written". This line is a LOWER BOUND read from atomics and carries no
//      counterexample payload, and stdout is the only place it can say so. A
//      handler that printed the numbers without the caveat would let a killed
//      run be quoted as a measurement -- which is the failure mode this arm was
//      built to end, not a new way to reach it.
//
//   4. That `Coverage report written to cov-report.json` is ABSENT. The handler
//      cannot write JSON: it runs in a signal context where malloc, iostream and
//      the log mutex are all unsafe, and a handler that deadlocks on the
//      allocator turns "partial data" into "no data and a hang". So the honest
//      statement is that the report was not written and the payload lives in
//      cov-ce-journal.json -- which is precisely why the journal (step 1) had to
//      exist before this arm did.
//
// raise(SIGTERM) is reached through --path-cov-fault-sigterm because the
// harness cannot deliver a real signal to the tool: it strips --timeout, and a
// test.desc is one invocation with no wrapper.
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
