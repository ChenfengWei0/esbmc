// ENTRY STORAGE pinned: the prescribed way out of the diagonal.
//
// Same contract as the `_diagonal` twin. There both coordinates were free, the
// path domains were the two sides of `bal >= amt`, and no box could separate
// them. Here `state.bal` is PINNED to 50, which turns the question back into one
// dimension — and in one dimension the interval is exact:
//   enc=2 (`amt <= bal`)  outer box amt in [0, 54]   certified [0, 44]
//   enc=3 (`amt >  bal`)  outer box amt in [45, max] certified [55, max]
// Both certified regions are subsets of the true domains ([0,50] and [51,max]);
// the ~9 of slack on each side is the ladder resolution (100/11), not an error.
//
// THE PIN IS PART OF THE ANSWER. Every box and region here describes the SLICE
// through `bal == 50`, not the whole domain, and the run says so on its own
// line. A test rendered from `[0,44]` without also requiring `bal == 50` would
// claim something about inputs that were never examined — which is why the pin
// is printed with the regions rather than being an invisible parameter of the
// measurement.
//
// The pin is applied to the ENTRY value: the coordinate is snapshotted at
// function entry and the snapshot is what the antecedent tests. Reading the live
// variable at the exit instead would pin a path that WRITES the variable to a
// value it only reaches on the way out, which is not a slice of the input space
// at all.
//
// Both regions were subsequently put through --path-cov-certify (with the pin
// expressed as the degenerate box `state.bal in [50,50]`): [0,44] comes back
// SUCCESSFUL, and [0,51] — past the real boundary — comes back FAILED at the
// OTHER path's exit.
pragma solidity ^0.8.0;

contract St {
    uint256 public bal;

    function set(uint256 v) external payable {
        bal = v;
    }

    function f(uint256 amt) external payable returns (uint256) {
        if (bal >= amt) {
            return 1;
        }
        return 0;
    }
}
