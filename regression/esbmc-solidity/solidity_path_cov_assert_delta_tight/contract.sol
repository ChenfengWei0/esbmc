// STAGE 3 -- the DELTA rung, and the unsigned-wrap guard behind it.
//
// Two paths over one scalar: one INCREASES `bal` by 7, the other DECREASES it
// by 7. The spec points `delta_dir: "inc"` at the DECREASING path with the
// widest possible window, [0, 2^256-1].
//
// IT MUST COME BACK REFUTED, and that is a control needing no fault injection.
// Candidate variables are unsigned (coord_expressible refuses signed outright),
// so on the decreasing path `post - pre` wraps to 2^256 - 7 -- which is inside
// [0, 2^256-1]. A naive `lo <= post - pre <= hi` therefore HOLDS there, and
// holds for the most natural spec a driver writes first ("any increase"). The
// `post >= pre` conjunct is the only thing that refuses it: delete that
// conjunct and this fixture flips green.
//
// THE CONSTRUCTOR IS LOAD BEARING. The first version of this fixture had no
// constructor and pinned `state.bal in [7, 100]`, and the mode REFUSED it as a
// VACUOUS REGION -- correctly: a fresh deploy leaves `bal` at 0, so no execution
// admits that region, and all eight candidates came back PASSED for want of an
// execution. `bal` is therefore initialised here and the region pins the value
// the constructor actually produces. (Without it the decrement would also
// underflow-revert on 0 under checked arithmetic.) That the vacuity gate caught
// this fixture, minutes after it was built, is the gate working on its author.
pragma solidity ^0.8.0;

contract Delta {
    uint256 bal;

    constructor() {
        bal = 1000;
    }

    function f(uint256 a) external payable returns (uint256) {
        if (a > 10) {
            bal = bal + 7;
            return 1;
        }
        bal = bal - 7;
        return 0;
    }
}
