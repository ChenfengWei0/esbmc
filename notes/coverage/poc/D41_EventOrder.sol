// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// DOES ANYTHING DOWNSTREAM OBSERVE AN EVENT, OR ITS ORDER?
//
// R0's remaining rung is "exit kind + revert reason + EVENTS AND THEIR ORDER".
// Exit kind shipped. The event half has TWO project documents disagreeing about
// whether it needs new mechanism, and they cannot both be right:
//
//   notes/r0-events-three-missing-layers.md : blocked at layer 1 -- there is no
//     per-path event observation channel, `path_ce_t` has 13 fields and none can
//     hold order, and a zero-argument event leaves no step at all.
//   EXECUTION_PLAN.md (§10, 2026-08-01) : an EventDefinition is compiled to a
//     real function symbol with an empty body, so an unqualified `emit` produces
//     a FUNCTION_CALL instruction ==> "R0's event rung needs NO new mechanism
//     for existence and order"; only the PAYLOAD is unreachable.
//
// A source reading settles neither, because D18 in this same directory is the
// standing example of a correct source reading whose behavioural consequence was
// refuted. So this is measured, not read.
//
// THREE CONTRACTS, ONE VARIABLE. Identical unit, identical branch, identical
// state write. They differ ONLY in the emits placed before the branch:
//
//   D41_NoEvent   : no events                       <- POSITIVE CONTROL
//   D41_EventAB   : emit Alpha(x); emit Beta(x);
//   D41_EventBA   : emit Beta(x);  emit Alpha(x);   <- same SET, other ORDER
//
// THREE-WAY DISCRIMINATOR, and each outcome is a different answer for R0:
//
//   (i)   all three reports identical apart from the contract name
//         ==> NO event channel reaches the report. The note is right, the plan's
//             "needs no new mechanism" is wrong, and R0's event rung is blocked.
//   (ii)  NoEvent differs from AB, and AB == BA
//         ==> EXISTENCE is observable, ORDER is not. R0 ships half a rung.
//   (iii) AB differs from BA
//         ==> existence AND order are observable. The plan is right and the rung
//             is cheap.
//
// WHY THE SET IS THE SAME IN AB AND BA: if the two contracts emitted different
// events, any difference between their reports would be explained by the names
// alone and could not distinguish (ii) from (iii). Holding the multiset fixed
// and permuting only the sequence is what makes ORDER the single variable.
//
// POSITIVE CONTROL, and it is the reason any of the above may be read at all:
// D41_NoEvent must enumerate paths and witness BOTH arms. If it reports zero
// paths -- or if all three do -- then the discriminator never fired and outcome
// (i) is indistinguishable from "the run did not work". That is the trap the
// first D20 run and version 2 of D18 both fell into, in this same directory.
//
// The branch exists ONLY to give the unit more than one path; it touches no
// event. The emits are unconditional and before it, so every path carries both.
//
// ---- EXPECTED, WRITTEN BEFORE RUNNING ----
// All three: same paths_total, same F/U split, both arms of `x > 10` witnessed.
// The prediction under test is that AB and BA are byte-identical apart from the
// contract name -- i.e. outcome (i) or (ii), NOT (iii).
//
// ---- MEASURED 2026-08-03, current build. OUTCOME (i). ----
//
// Positive control fires: D41_NoEvent instruments 3 complete paths across 1
// unit and both arms are witnessed (`run:path:7` and `run:path:6` both FAILED,
// i.e. both refuted, i.e. both reached). So "no difference" below is a result
// and not a dead run.
//
// All three reports carry 3 claims. Comparing them leaf by leaf, the COMPLETE
// list of differing values is:
//
//     claims[*].decisions[*].line     62,63 -> 77,80 -> 94,97
//     claims[*].path_function         ...@D41_NoEvent@F@run#23 -> ...#62 -> #101
//
// That is line numbers and the contract's own name. NOTHING ELSE DIFFERS. Two
// unconditional emits contribute no path, no decision, no field, no counter.
// AB and BA -- same event multiset, opposite sequence -- are indistinguishable.
//
// ==> The event half of R0 has NO observation channel reaching the report, for
//     EXISTENCE let alone ORDER. r0-events-three-missing-layers.md is right.
//     EXECUTION_PLAN.md's "needs no new mechanism for existence and order" is
//     WRONG as a statement about what is observable downstream. Both can be
//     reconciled: an EventDefinition may well become a function symbol in the
//     GOTO program, but nothing carries that fact into the report, and the plan
//     inferred an observable from a lowering.
//
// SCOPE of the runs above: they were made WITHOUT the emission flag and so
// produced no Foundry section at all. They measure the REPORT channel only.
//
// ---- THE EMITTER SIDE, MEASURED SEPARATELY. SAME ANSWER. ----
//
// Re-run identically plus `--generate-foundry-testcase`. Positive control is
// much stronger here because each cell prints its own census, and all three are
// the same:
//
//     Path Exits: normal 2, revert 1, undetermined 0
//     Path Status: F 3, I 0, U 0
//     Generated Foundry coverage test with 3 case(s)
//     Foundry: 2 call(s) emitted bare (exit census confirmed normal; ...)
//
// The three generated tests are 36 lines each. After erasing the contract name,
// the COMPLETE diff between any two of them is three comment lines carrying the
// mangled unit id:
//
//     // claim: sol:@C@CONTRACT@F@run#23:path:7     (NoEvent)
//     // claim: sol:@C@CONTRACT@F@run#62:path:7     (EventAB)
//     // claim: sol:@C@CONTRACT@F@run#101:path:7    (EventBA)
//
// `#23`/`#62`/`#101` is the AST node id of the function, i.e. contract identity
// again. No `Alpha`, no `Beta`, no `vm.expectEmit`, no `vm.recordLogs` in any of
// the three. A generated test for a unit that emits two events is byte-identical
// to one for a unit that emits none.
//
// ==> The event rung needs NEW MECHANISM AT BOTH LAYERS, not just a field. The
//     report cannot carry the observation and the emitter would have nothing to
//     render from if it could.
//
// ---- BUT THE INFORMATION EXISTS ONE LAYER DOWN, ORDER AND ALL ----
//
// The two runs above measure the REPORT and the EMITTER. Neither tests the GOTO
// program, and which layer is missing decides whether this rung is a field to
// add or a front-end change. Dumped with --goto-functions-only:
//
//     7262: FUNCTION_CALL:  Alpha(x)      <- D41_EventAB, in source order
//     7264: FUNCTION_CALL:  Beta(x)
//     7317: FUNCTION_CALL:  Beta(x)       <- D41_EventBA, in ITS source order
//     7319: FUNCTION_CALL:  Alpha(x)
//     7719: Alpha (sol:@C@D41_EventAB@F@Alpha#28):
//     7720: // 3403 ... line 63 function Alpha
//     7721: END_FUNCTION // Alpha
//
// So EXECUTION_PLAN.md's source claim is CORRECT and now measured, not argued:
// an EventDefinition is compiled to a real function symbol with an empty body,
// and an unqualified `emit` becomes a FUNCTION_CALL instruction. The two
// emission ORDERS are visibly different in the GOTO program.
//
// ==> Revised, and this is the actionable form: the observation is NOT missing
//     from the program. It is present, per-unit, in source order. What is
//     missing is exactly ONE HOP -- the path walk does not record FUNCTION_CALL
//     steps into the path's identity, so nothing survives into the report, and
//     the emitter is starved downstream of that. The plan was right about the
//     lowering and wrong only in inferring an OBSERVABLE from it; the note was
//     right about the channel. Both halves now have a measurement.
//
// ---- ONE HOP, BUT IT NEEDS A MARKER FIRST ----
//
// "Exactly one hop" is one step short, and the missing step is the thing that
// would have made a naive implementation wrong. Read out of the front end:
//
//   solidity_convert_decl.cpp:1695  EventDefinition -> add_empty_body_node()
//                                   then get_function_definition(), i.e. the
//                                   SAME path an ordinary function takes
//   solidity_convert_decl.cpp:1842  add_empty_body_node() gives the event an
//                                   empty Block body
//
// So an event's symbol is a perfectly ordinary function symbol with an empty
// body, and NOTHING marks it as an event. "Empty body" is not a discriminator:
// an interface stub, an abstract override and a no-op setter are all empty, and
// a recorder keyed on emptiness would file them as emitted events. That is the
// always-true-reader shape, and it would have been invisible on this fixture
// because D41 contains no interface and no abstract contract -- the fixture
// would have gone green on a broken recorder.
//
// THE PRECEDENT FOR THE FIX ALREADY EXISTS IN THIS PIPELINE. The DFS already
// reads a front-end stamp off a location:
//
//   goto_coverage.cpp:5906   d.synthetic_abi_gate = l.get_bool("sol_abi_value_gate");
//
// So the shape is: stamp the event's symbol/location `sol_event` in the front
// end, and have the path walk read it. Decomposed:
//
//   1. front end   stamp EventDefinition-derived symbols
//   2. path walk   on a FUNCTION_CALL whose callee carries the stamp, append
//                  (name, position) to the current prefix -- alongside
//                  note_decision / dec_table, which is the exact precedent for
//                  a per-prefix interned table
//   3. report      an `events` array per claim, beside `decisions`
//   4. regression  THIS file: EventAB and EventBA reports must DIFFER, and
//                  NoEvent must carry an empty array rather than no field
//
// Step 4's negative control is the one that matters, and it is why the fixture
// permutes the order rather than changing the set: a recorder that captures
// existence but drops order passes an existence test and fails this one.
//
// ---- STEP 1 WAS ATTEMPTED, TWO WAYS, AND NEITHER ARRIVES ----
//
// Built and measured, not reasoned about. The front end stamped the emit
// STATEMENT in solidity_convert_stmt.cpp's EmitStatement case -- first on the
// expression's LOCATION, then on both the location AND the expression irep --
// and goto_coverage.cpp counted, per unit, FUNCTION_CALL instructions carrying
// either stamp, reporting which carrier arrived.
//
//   attempt 1  location only          -> no emit-site line on any fixture
//   attempt 2  location + code irep   -> no emit-site line on any fixture
//
// On all three contracts, including the two that emit. The logs differ by
// exactly the two extra lines the extra calls produce (99 vs 101), so the calls
// ARE in the program -- what does not survive the Solidity-to-GOTO conversion
// is the stamp.
//
// One real hazard was found and fixed on the way, and it was NOT the cause.
// `get_statement` ends with `new_expr.location() = loc`, a whole-object
// assignment that replaces the location irep and discards anything set on it
// earlier -- the file's own comments at the IfStatement and while cases
// already name this trailing assignment as the hazard they work around. The
// stamp was moved onto the expression and re-applied to the location after
// that assignment. The census still counted zero.
//
// ---- THE ACTUAL CAUSE: THE CALL IS ALREADY GONE ----
//
// Attempt 3 made the census report `calls_seen` as well, so that "no stamped
// call" could not hide two different causes behind one silence. All three
// units, at the moment the path pass sees them:
//
//   unit 'sol:@C@D41_NoEvent@F@run#23'  : 2 function call(s), NONE stamped
//   unit 'sol:@C@D41_EventAB@F@run#62'  : 2 function call(s), NONE stamped
//   unit 'sol:@C@D41_EventBA@F@run#101' : 2 function call(s), NONE stamped
//
// THE SAME TWO CALLS IN ALL THREE -- including D41_NoEvent, which emits
// nothing. The emitting units have no extra calls at all by then.
//
// ⇒ The emit calls are visible in --goto-functions-only and GONE by the time
//   the path pass runs. An event body is empty, so they are inlined away
//   (internal-call expansion, or a general cleanup) before the census. The
//   stamp was never the problem: with a perfect stamp the recorder would still
//   have had nothing to record, because at that point in the pipeline the emit
//   is no longer a call.
//
// ⇒ REVISED DESIGN. The observation has to be captured BEFORE expansion, or
//   expansion has to be taught to keep emit calls. Stamping the callee SYMBOL
//   -- the other candidate -- does not help either, for the same reason: it
//   addresses which call is an event, not whether any call survives.
//
// THE CENSUS IS WHY THIS IS A MEASUREMENT AND NOT A BUG SHIPPED. It was written
// BEFORE the recorder, deliberately, on the argument that a stamp which never
// arrives would make the recorder silently record nothing -- and a fully wired,
// always-empty channel is indistinguishable from a contract that emits nothing.
// That is exactly what happened, and it was caught in one rebuild instead of
// after a path-recorder, a report field and a regression had been built on top.
//
// BOTH EDITS WERE REVERTED. A stamp that never fires and a census that can never
// print are dead code, and leaving them in the tree is the failure this project
// has recorded twice. The tree is back at the committed source; what is kept is
// this measurement.
//
// ⚠ POSITIVE CONTROL for this dump: the three files are the same LENGTH
// (753767) but are not byte-identical, so --contract does change the dump and
// the comparison is between three different things. Had they been identical the
// comparison would have been between three copies of one artefact and the
// script says so before printing anything else.
//
// ---- MY FIRST INSTRUMENT WAS CONFOUNDED AND SAID (iii). KEPT, NOT REPLACED. ----
//
// v1 of the comparison asked only whether the three reports were EQUAL, plus a
// token test for the strings "Event"/"emit". It answered "(iii): existence AND
// order are observable" -- the exact opposite of the truth. Two faults, both in
// the instrument, both guaranteed to fire with or without an event channel:
//   1. the three contracts sit at different LINE NUMBERS in one file, and the
//      emits push the branch down, so `src`/`line` differences are structural;
//   2. the contract NAMES contain the substring "Event", so the token test is
//      true even in the no-event control.
// The repair was to print the differing VALUES instead of the fact that values
// differ. A discriminator that cannot say WHY two things differ will happily
// report a line number as a discovery.

contract D41_NoEvent {
    uint256 public v;

    function run(uint256 x) external {
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }
}

contract D41_EventAB {
    event Alpha(uint256 got);
    event Beta(uint256 got);

    uint256 public v;

    function run(uint256 x) external {
        emit Alpha(x);
        emit Beta(x);
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }
}

contract D41_EventBA {
    event Alpha(uint256 got);
    event Beta(uint256 got);

    uint256 public v;

    function run(uint256 x) external {
        emit Beta(x);
        emit Alpha(x);
        if (x > 10) {
            v = 1;
        } else {
            v = 2;
        }
    }
}
