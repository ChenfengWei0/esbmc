// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// A PATH THAT NEEDS A NARROWING TRUNCATION -- WHICH THE CHAIN PERFORMS SILENTLY
// AND `--path-cov-arith-resolve` MAY PROVE "REACHABLE ONLY BY REVERTING".
//
// THE CHAIN'S ANSWER IS ALREADY MEASURED, in D19_PanicSemantics.t.sol, 7/7 under
// forge 1.7.1 / solc 0.8.34 with its positive controls firing:
//
//   uint8(300) == 44, no revert          narrowing TRUNCATES
//   200 << 1  == 144, no revert          shifts TRUNCATE
//   200 + 200          Panic(0x11)       checked arithmetic REVERTS
//   1 / 0              Panic(0x12)       division by zero REVERTS
//
// THE MECHANISM AT RISK. `--path-cov-arith-resolve` re-solves a witnessed path
// claim after converting EVERY assert whose `location.property()` is "overflow"
// or "division-by-zero" into an ASSUME (bmc.cpp:3673-3690). `property()` is a
// machine field with more than one producer: besides `overflow_check`, both
// `cast_overflow_check` (goto_check.cpp:319-320) and a narrowing-ASSIGNMENT
// check (goto_check.cpp:1252-1261) stamp the same value, and `shift_check`
// routes left shifts through `overflow_check` as well.
//
// So the re-solve can assume "this conversion does not truncate". On a path that
// REQUIRES the truncation the conjunction is then UNSAT -- and the code reads
// UNSAT as a PROOF that the path is reachable only through a checked-arithmetic
// revert, records it in `arith_revert_only_paths`, and REFUSES to emit the case
// (bmc.cpp:4003-4042).
//
// That refusal would be FALSE. The path is reachable on chain by an ordinary
// call. The cost is not a red test but a silently missing one, which is harder
// to notice: a refusal that is counted still reads as the mechanism working.
//
// `narrowGate` is built so the truncation is NOT optional. `v == 0 && x != 0`
// holds exactly when x is a nonzero multiple of 256 -- x = 256 is a witness, and
// there is no witness at all if the conversion is assumed not to truncate.
//
// ---- EXPECTED, WRITTEN BEFORE RUNNING ----
//
// A. `--overflow-check` alone, no re-solve:
//      the `tag = 1` path is F with a counterexample whose x is a nonzero
//      multiple of 256, and a Foundry case is emitted for it.
//
// B. `--overflow-check --path-cov-arith-resolve`:
//      IF the defect is real -> that path is counted under "PROVEN reachable
//      only through a checked-arithmetic revert" and its case is REFUSED. The
//      run's own summary line says so, so no log archaeology is needed.
//      IF it is not real -> the counts are unchanged from A, and the narrowing
//      claim either is not produced under plain `--overflow-check` or is not
//      caught by the property() filter. Either way the answer is in the summary.
//
// The two runs differ in ONE flag. Whichever way it goes, the difference between
// them is the finding.
//
// `addGate` IS THE POSITIVE CONTROL AND IT MUST BE REFUSED. Its path genuinely
// needs a wrap, exactly like D16_OnlyByOverflow, so a run that refuses NEITHER
// path has the re-solve switched off or broken, and is distinguishable from a
// run that correctly refuses only `addGate`.
//
// `plainGate` IS THE NEGATIVE CONTROL AND IT MUST NEVER BE REFUSED. No
// arithmetic at all, so nothing can be assumed away; if it is refused, the
// filter is catching claims that have nothing to do with arithmetic and the
// diagnosis above is not the whole story.

contract D20_FalseRevertOnly {
    uint256 public tag;

    // THE EXPERIMENT. Reachable on chain (x = 256), and reachable ONLY through
    // a truncation that the chain performs without reverting.
    function narrowGate(uint256 x) external {
        uint8 v = uint8(x);
        if (v == 0 && x != 0) {
            tag = 1;
        }
    }

    // POSITIVE CONTROL: genuinely reachable only by wrapping a checked add, so
    // a correct re-solve MUST prove this one revert-only.
    function addGate(uint8 a) external {
        uint8 b = 200;
        uint8 s = b + a;
        if (s < 200) {
            tag = 2;
        }
    }

    // NEGATIVE CONTROL: no arithmetic anywhere.
    function plainGate(uint256 x) external {
        if (x > 1000) {
            tag = 3;
        }
    }
}
