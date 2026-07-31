// THE VACUITY VERDICT THE UNWIND BOUND MADE UP, and the FIRST of a must-flip
// pair with solidity_path_cov_certify_truncation_absent_still_certified. The two
// directories share this contract and this command line; they differ only in the
// spec, and therefore only in whether the loop is truncated.
//
//     enc=66 depth=6, box `a in [11,100]`      -> loop cut at the bound
//                                              -> RESULT: UNDECIDED-TRUNCATED (here)
//     enc=10 depth=3, box `a in [11,100],
//                          n in [1,1]`         -> loop never reaches the bound
//                                              -> RESULT: CERTIFIED  (sibling)
//
// WHY enc=66 IS THE PATH THAT BREAKS. `--solidity-path-coverage` enumerates
// paths by following each back edge at most `path_cov_unwind` (4) times, and it
// then forces symex's `--unwind` to the same 4 so the two agree
// (esbmc_parseoptions.cpp:4288-4304). It ALSO forces `no-unwinding-assertions`
// (:4305). So at the deepest enumerated layer -- exactly 4 back edges -- symex
// reaches `loop_bound_exceeded`, takes the `else` branch, and emits
// `assume(!guard)` instead of an unwinding assertion (symex_goto.cpp:482-506).
// That assumption removes precisely the executions that walk the layer the
// enumeration just instrumented, so the non-vacuity witness
// `assert(tr != 66 || cnt != 6)` HOLDS -- and the tool used to answer
//
//     RESULT: VACUOUS -- the box admits NO execution that walks path enc=66
//
// which is a statement about the region and is FALSE. It is a statement about
// the bound. The enumeration says the path exists; the exploration was cut
// before it could be walked; those are different facts and the confident word
// belongs to neither.
//
// This is the same mechanism measured on aqua `Aqua.dock` enc=12, where
// `--path-cov-certify` answered CERTIFIED and `--path-cov-assert` answered
// VACUOUS for the IDENTICAL region -- the whole difference being one truncated
// library loop (`__memset_impl`, src/c2goto/library/string.c:298) that
// `--unwindset 64:512` brings back. The loop here is a source loop rather than a
// library one so the fixture is deterministic and needs no flat 1inch input, but
// the assumption, the bound and the false verdict are the same three lines.
//
// `n` IS A SEPARATE PARAMETER FROM `a` ON PURPOSE. It is what makes the trip
// count nondet: a constant-trip loop truncates to `assume(false)`, which kills
// the whole run (measured: `Generated 0 VCC(s)` and the entry-liveness audit
// aborts) rather than deleting some executions. A nondet trip count is what
// produces the SILENT case this gate exists for.
//
// PASSING THIS HALF ALONE IS NOT EVIDENCE. An implementation that answered
// UNDECIDED-TRUNCATED whenever any loop was truncated also passes it, and would
// destroy every verdict on every contract with a loop. The sibling directories
// are what pin the other two corners:
//   * ..._truncation_absent_still_certified -- no truncation, verdict stands;
//   * ..._truncated_refuted_unaffected      -- truncation, but the witness WAS
//                                              refuted, so the verdict stands;
//   * ..._certify_vacuous_state_box_refused -- a genuinely empty box and no loop
//                                              at all, so VACUOUS is still said.
pragma solidity ^0.8.0;

contract Trunc {
    uint256 public s;

    constructor() {
        s = 0;
    }

    function f(uint256 a, uint256 n) external payable returns (uint256) {
        for (uint256 i = 0; i < n; i++) {
            s += 1;
        }
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
