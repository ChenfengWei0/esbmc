// A COUNTEREXAMPLE THAT ONLY EXISTS IN MEMORY IS NOT A DELIVERABLE.
//
// MEASURED, on the whole-contract run of `aqua` at --memlimit 8g: the run died
// 51.5% of the way through the solve having DECIDED 938 claims and REFUTED 5 of
// that contract's 15 complete paths -- and produced no artefact at all. All five
// were genuine (they are in the 20 g run's F set). The reason is structural, not
// bad luck: `cov-report.json` is written exactly once, by report_coverage, which
// sits AFTER the per-claim job loop and INSIDE the try that the allocation
// failure unwinds. A caught OOM therefore costs the ENTIRE report, not part of
// it, and an uncaught one costs the process.
//
// The cross-run covered set (--coverage-covered-set) is written mid-solve and
// would have kept the five path IDS -- but no collector in this project has ever
// passed that flag, and until this change the file carried no counterexample
// PAYLOAD anyway. A path recorded as covered with no inputs can never produce a
// test, and the round that could still have produced them is the round that
// skips the path. So enabling that mechanism first would have converted a
// temporarily lost witness into a permanently payload-less one.
//
// The journal has no such gate. Whenever the run asked for the payload at all
// (--cov-report-json) it is refreshed by an atomic .tmp+rename at the moment a
// path is WITNESSED, and it is never read back in, so it cannot accumulate
// across runs or change what a re-run does.
//
// WHY THIS TEST NEEDS A FAULT INJECTOR. The mechanism only runs on a run that
// does NOT reach a clean exit, and a test description is a single invocation
// with no environment of its own -- the harness additionally STRIPS --timeout
// and --memlimit from the argument list (testing_tool.py UNSUPPORTED_OPTIONS).
// There is no way to produce a dying run from here except to ask the tool for
// one, so --path-cov-fault-after N is a shipped option rather than a throwaway
// build. An untested rescue path is exactly the shape this tool has already
// shipped twice: a function that was written and never called, and a guard whose
// answer was always true.
//
// WHAT IS PINNED, and why each line is load-bearing:
//   * the journal line names the CLAIM INDEX ("after claim 1 of 4"). Without it
//     the line is compatible with the old end-of-run write and the test would
//     pass on a build that had changed nothing about WHEN the payload lands.
//   * the counts are read back OUT OF THE PUBLISHED FILE, not taken from the
//     in-memory maps that produced it, so "1 with non-empty inputs" is a
//     statement about the disk. A census of what the writer believes it wrote
//     would have printed correct-looking numbers throughout the period in which
//     nothing called the covered-set writer at all.
//   * `Coverage report written to cov-report.json` must be ABSENT. That is the
//     whole claim: the payload survived a run that never got to write a report.
//
// The contract is chosen so that every enumerated path is feasible (F 4, I 0,
// U 0), which makes the FIRST decided claim a witnessed one and the injected
// fault land after exactly one payload has been journalled.
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
