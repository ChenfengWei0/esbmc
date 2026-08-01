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
//
// ---- MEASURED 2026-08-01. THE SUSPICION IS REFUTED AND A WORSE ONE CONFIRMED --
//
// FIRST RUN WAS INCONCLUSIVE BY THIS FILE'S OWN CRITERION and is recorded rather
// than quietly redone: `addGate` was written at uint8, the positive control did
// NOT fire (0 paths proven revert-only), and the file already said that a run
// refusing neither path has the re-solve off or broken. A negative result with a
// dead positive control is not a negative result. `addGate256` was added -- the
// uint256 shape the proof arm has been measured to fire on -- and the uint8 one
// kept, turning a broken control into an experiment.
//
// SECOND RUN, `--overflow-check --path-cov-arith-resolve`, decoded with the
// polarity rule (`branch_claim` is the PROBE GUARD and is FALSE on the edge
// actually taken):
//
//   claim                            edge actually taken   witness        verdict
//   addGate256 `!(s<500)` fallthru   s < 500  = WRAPPED    a = 2^256-4    arith_revert_only TRUE
//   addGate    `!(s<200)` fallthru   s < 200  = WRAPPED    a = 248        arith_revert_only None
//   narrowGate `!(v==0&&x!=0)` ft    condition TRUE        x = 2^256-256  None  (correct)
//   plainGate  (all three)           --                    --             None  (correct)
//
// 1. THE FALSE REFUSAL DOES NOT REPRODUCE. `narrowGate` is witnessed with a
//    genuinely truncating input and is NOT proven revert-only. The negative
//    control is clean too. The narrowing producer either is not present under
//    plain `--overflow-check` or is not caught by the filter.
//
// 2. THE SAME RUN SHIPS A RED TEST, in the OPPOSITE direction -- a MISSED
//    refusal, not a false one. `addGate`'s wrapping path is witnessed at
//    a = 248 (200 + 248 = 448, truncated to 192 < 200), reported `exit_kind:
//    normal` with `arith_revert_only: None`, so it renders as a bare call
//    asserting the call succeeds. D19_PanicSemantics.t.sol already measured the
//    chain's answer for exactly this: uint8 `200 + 200` reverts Panic(0x11). So
//    the emitted test is RED on the unmodified contract.
//
// 3. THE MECHANISM IS VISIBLE IN THE COUNTS. Every claim in the run reports the
//    SAME "7 arithmetic condition(s)" -- including all three `plainGate` paths,
//    which contain no arithmetic whatsoever. The conditions are global (harness
//    and constructor), and `addGate`'s own uint8 addition contributes NONE. So
//    no overflow claim is generated for narrow-width arithmetic under
//    `--overflow-check`, and the re-solve cannot assume what was never emitted.
//
// WHAT THIS FILE NOW PINS, and it is not what it was written to pin: the proof
// arm is WIDTH-DEPENDENT. uint256 is detected, uint8 is not, from one source
// shape differing only in the declared type. Until that is fixed, "0 paths
// proven reachable only through a checked-arithmetic revert" means "none at
// 256 bits", not "none".

contract D20_FalseRevertOnly {
    uint256 public tag;
    uint256 public big;

    constructor() {
        big = 500;
    }

    // THE EXPERIMENT. Reachable on chain (x = 256), and reachable ONLY through
    // a truncation that the chain performs without reverting.
    function narrowGate(uint256 x) external {
        uint8 v = uint8(x);
        if (v == 0 && x != 0) {
            tag = 1;
        }
    }

    // POSITIVE CONTROL AT uint256 -- literally D16_OnlyByOverflow's shape, which
    // is the one width the proof arm has been MEASURED to fire on. If this does
    // not get proved revert-only, the re-solve is off or broken and no other row
    // of this run can be read.
    function addGate256(uint256 a) external {
        uint256 s = big + a;
        if (s < 500) {
            tag = 4;
        }
    }

    // THE SAME EXPERIMENT AT uint8, and it is an EXPERIMENT rather than a second
    // control. Measured on the first run of this file: `addGate` was NOT proved
    // revert-only, while `s = 200 + a` under `uint8` is unsatisfiable for
    // `s < 200` once no-overflow is assumed. Either uint8 checked arithmetic
    // produces no `overflow` claim at all, or it produces a NARROWING one
    // (goto_check.cpp:1252-1261) -- which is the very producer this file is
    // about. Whichever it is, the difference between this row and `addGate256`
    // is the finding.
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
