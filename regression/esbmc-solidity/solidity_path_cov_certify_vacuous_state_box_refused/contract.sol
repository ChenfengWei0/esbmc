// THE VACUOUS CERTIFICATE, and the FIRST HALF of a must-flip pair with
// solidity_path_cov_certify_nonvacuous_state_box_certified. The two specs
// differ in ONE decimal.
//
//     box `state.s in [0,0]`, a in [11,100]  ->  RESULT: VACUOUS   (here)
//     box `state.s in [7,7]`, a in [11,100]  ->  RESULT: CERTIFIED (sibling)
//
// The constructor assigns `s = 7` and state variables are NOT havoc'd at
// --solidity-max-tx 1, so at function entry `s` is 7 on every execution. The
// box therefore admits NOTHING -- and every one of the four structural gates in
// front of it waves it through, because every one of them is SYNTACTIC:
//
//     lo <= hi            0 <= 0        passes
//     bounded twice       one bound     passes
//     holes empty it      no holes      passes
//     fits the type       0 fits uint256 passes
//
// BEFORE THE NON-VACUITY WITNESS THIS DIRECTORY PRINTED `VERIFICATION
// SUCCESSFUL` AND EXITED 0. Nothing executed, so every exit assert held for
// want of an execution, and the output was byte-comparable with a real
// certificate. That is a FALSE certificate, not a weak one: there is nothing to
// reinterpret afterwards, and a driver recording it has recorded a region that
// contains no input at all.
//
// What makes this the common case rather than an exotic one is the entry state.
// Real path conditions are mostly guarded by storage, so a driver generalising
// over `state.<field>` coordinates writes boxes of exactly this shape; one
// stale or mis-parsed decimal is all it takes.
//
// PASSING THIS HALF ALONE IS NOT EVIDENCE. An implementation that reported
// VACUOUS unconditionally also passes it. The sibling is what shows the witness
// can be satisfied, so the pair -- and only the pair -- tests that the two
// outcomes are consistently OPPOSITE.
pragma solidity ^0.8.0;

contract Vac {
    uint256 public s;

    constructor() {
        s = 7;
    }

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            return 1;
        }
        return 0;
    }
}
