// `--focus-function` names a SET, and this pins that two of three units are
// enumerated while the third is not.
//
// WHY THIS DIRECTION NEEDS ITS OWN TEST. The single-name behaviour is already
// pinned by `solidity_path_cov_focus_function_narrows_instrumentation`, and a
// multi-name value used to be a HARD ERROR rather than a wrong answer: the
// frontend compared the whole option value against one function name, so
// `--focus-function a,b` looked for a function literally called "a,b", found
// none, and exited with
//     ERROR: --focus-function 'a,b' is not a public/external function of
//            contract 'C'
//     ERROR: CONVERSION ERROR
// So the failure mode this test replaces was loud. The failure mode it now
// guards against is the SILENT one: three places have to agree on which
// functions a value selects -- the frontend validator, the frontend's dispatcher
// filter, and the path-coverage pass's instrumentation narrowing -- and if the
// dispatcher and the pass ever disagreed, a unit the harness CAN enter would
// carry no claims at all. That reads as an honest zero, not as a hole. They
// share one parser (util/focus_function.h) precisely so the disagreement is
// unrepresentable, and this test is what would notice if a fourth reader grew
// its own.
//
// THE CONTRACT: three units, two named, each with one decision and no state
// guard, so every enumerated path is witnessed at tx=1 and the numbers are
// forced rather than merely observed:
//
//     units enumerated       2   (`a` and `b`)
//     units skipped by focus 1   (`c`)
//     paths instrumented     6   (3 per unit, all feasible)
//     Path Exits             normal 4, revert 2
//     Path Coverage          100%
//
// THREE paths per unit, not two, and the pin was written as two before it was
// measured. Each unit's source decision gives the two NORMAL exits; the third is
// the REVERT exit of the nonpayable ABI value gate (`msg.value != 0` on a
// non-payable entry), which is a real edge of the model and one of the exit
// kinds this method enumerates on purpose. The distribution line pins the
// pre-expansion count at 4 -- two per unit -- which is where the source-decision
// figure lives, so both numbers are visible and a change to either is loud.
//
// `c`'s two paths are ABSENT FROM THE DENOMINATOR, which is the whole point:
// the dispatcher cannot enter `c` in this run, so no exploration could witness
// its paths, and counting them would make the reported coverage a contract-level
// number wearing a unit-level label.
//
// The three functions are deliberately independent -- no internal calls -- so
// this test says nothing about expansion and cannot go red for an expansion
// change. `solidity_path_cov_focus_function_keeps_callee_decisions` is the test
// that owns that axis.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function a(uint256 v) public {
        if (v > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function b(uint256 v) public {
        if (v > 3) {
            x = 3;
        } else {
            x = 4;
        }
    }

    function c(uint256 v) public {
        if (v > 5) {
            x = 5;
        } else {
            x = 6;
        }
    }
}
