// THE SECOND HALF OF THE MUST-FLIP PAIR with
// solidity_path_cov_certify_vacuous_state_box_refused. Identical contract,
// identical enc/depth, identical bound on `a` -- the ONLY difference is one
// decimal in the `state.s` bound.
//
//     box `state.s in [0,0]`, a in [11,100]  ->  RESULT: VACUOUS   (sibling)
//     box `state.s in [7,7]`, a in [11,100]  ->  RESULT: CERTIFIED (here)
//
// This half is what stops the gate from being a check that says VACUOUS to
// everything. A detector with no direction that must flip cannot testify for
// itself; the sibling shows the witness can fail, this one shows it can be
// satisfied, and the property under test is that the two are consistently
// OPPOSITE. Deleting either half leaves something that still looks protective
// and is not -- the same discipline as the `_box_inside` / `_box_straddles`
// pair, which exists for exactly this reason one layer up.
//
// It also pins the CONSEQUENCE of the witness, which is not cosmetic: the
// witness claim is REFUTED on a run that certifies, so this directory prints
// `VERIFICATION FAILED`. That is why the tool now states `RESULT: CERTIFIED`
// on a line of its own and why the driver reads THAT line. A regression pinning
// only the verdict line would have recorded this certificate as a refutation.
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
