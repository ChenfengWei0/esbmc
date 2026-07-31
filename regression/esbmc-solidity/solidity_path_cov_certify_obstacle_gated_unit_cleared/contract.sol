// THE MUST-FLIP TWIN of solidity_path_cov_certify_obstacle_gated_unit_refused.
//
// SAME contract, SAME spec (`unit a, enc=2, depth=1, v in [0,1]`). The two
// directories differ in exactly ONE token: `--unwind 1` there, `--unwind 3`
// here.
//
//   --unwind 1  ->  ERROR: REFUSING THE QUERY: this unit is a NAMED OBSTACLE
//                   (lost decision: no; calls a gated unit: yes)
//   --unwind 3  ->  the query is emitted, and RESULT: REFUTED
//
// WHY THE FLIP IS THE TEST. At `--unwind 1` the chain a -> b -> c is expanded
// only as far as `b`, so `a` still calls the UNIT `c` through `c`'s OWN body --
// which carries the synthesised ABI value gate that models an EXTERNAL entry.
// The model then admits "the callee reverted because the transaction carried
// value" inside a caller that on-chain proceeds, so a box certified over that
// unit could contain inputs whose modelled execution cannot happen. At
// `--unwind 3` the whole chain is expanded, every copy is gate-free, the unit is
// clean, and the certification query runs and answers on its merits.
//
// A gate with no direction that must flip cannot testify for itself. The refused
// half alone is satisfied by an implementation that refuses EVERY certification;
// this half is what shows it refuses only the obstructed ones. Deleting either
// leaves something that still looks protective and is not.
//
// This half additionally pins that the run reaches a REAL verdict rather than
// merely getting past the gate: `RESULT: REFUTED ... Non-vacuity WAS witnessed`.
// Without that clause a box that cleared the obstacle gate and then certified
// VACUOUSLY would satisfy a weaker pattern, and the pair would pass while
// testing nothing about the query itself.
//
// THE GATE READS PER-UNIT LOCALS, NOT `named_obstacle_paths`. That map is filled
// by the insertion loop the certify branch `continue`s past, so in this mode it
// is EMPTY -- a gate reading it would never fire while looking exactly like a
// gate. The trap was written down on the stage-3 side before either gate
// existed; this pair is what shows this one avoided it.
//
// The enumeration-side behaviour of this same contract is pinned separately by
// solidity_path_cov_residual_unit_call_obstacle (unwind 1) and
// solidity_path_cov_residual_unit_call_expanded (unwind 3). Those two pin that
// the obstacle is DETECTED; this pair pins that certification ACTS on it.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function a(uint256 v) public {
        if (v > 1) {
            x = 1;
        }
        b(v);
    }

    function b(uint256 v) public {
        if (v > 2) {
            x = 2;
        }
        c(v);
    }

    function c(uint256 v) public {
        if (v > 3) {
            x = 3;
        }
    }
}
