// S5 -- THE SECOND HALF of the bool must-flip pair. Same contract, same path,
// the opposite box on the same coordinate:
//
//     box `state.flag in [1,1]`  ->  RESULT: CERTIFIED (sibling)
//     box `state.flag in [0,0]`  ->  RESULT: VACUOUS   (here), exit 1
//
// ---- IT IS VACUOUS, NOT REFUTED, AND THE DIFFERENCE IS THE WHOLE POINT ----
//
// The design note for S5 wrote this half down as "must be REFUTED". That is
// wrong, and wrong in the dangerous direction. REFUTED means an input the box
// ADMITS leaves the path -- it requires an execution to exist. Here the
// constructor sets `flag = true` and contract state is not havoc'd at
// --solidity-max-tx 1, so on every execution `flag` is true at entry and the
// assumption `flag == false` admits NOTHING. The non-vacuity witness at this
// path's own exit is therefore not refuted, and report_path_cov_certify prints
// VACUOUS and exits 1 before any exit verdict is even read.
//
// Pinning REFUTED here would have been satisfiable only by DISABLING the
// non-vacuity gate -- which is the false-certificate hole that gate exists to
// close, and the outcome would then have been byte-comparable with the sibling's
// certificate. A fixture that can only pass by reopening a closed hole is worse
// than no fixture.
//
// ---- WHAT THE PAIR ACTUALLY TESTS ----
//
// That the bool bound REACHED THE FORMULA. `state.flag` is true on every
// execution of this model, so a query that parsed the box, counted it in
// "assumed 1 input bound(s)", and then emitted no conjunct at all would CERTIFY
// here exactly as it does in the sibling -- both halves green, nothing measured.
// The two halves differ in one decimal and produce opposite outcomes only if the
// allowed-set disjunction (`flag == false` here, `flag == true` there) is really
// in the assumption.
//
// Note what the four structural gates make of this box: lo <= hi passes, the
// name is bounded once, there are no holes, and 0 fits [0, 1]. Every one of them
// is SYNTACTIC and none can see that the constructor never produces this state.
// That the fourth one says [0, 1] rather than [0, 255] is itself part of S5:
// `bool_type2t::get_width()` returns 8 for the memory-model byte, so the generic
// `2^width - 1` range check would have admitted `state.flag in [0, 200]`.
pragma solidity ^0.8.0;

contract Flag {
    bool public flag;
    uint256 public sink;

    constructor() {
        flag = true;
    }

    function f() external payable returns (uint256) {
        if (flag) {
            sink = 1;
            return 1;
        }
        sink = 2;
        return 0;
    }
}
