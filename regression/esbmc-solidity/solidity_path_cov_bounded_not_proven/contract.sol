// The I/U distinction: `a > 10 && a < 5` is genuinely unreachable, but only an
// UNBOUNDED run may say so.
//
// Paired with solidity_path_cov_infeasible, which runs the SAME contract under
// `--solidity-max-tx 1` and pins `Path Status: F 2, I 0, U 1` — the identical
// path is reported U there. This test pins `F 2, I 1, U 0` under
// `--solidity-max-tx 0`.
//
// Together they lock the rule the tri-state exists for: a claim that merely
// HELD within an exploration bound is undecided, not proven. Reporting it as I
// would turn "we did not reach it with this budget" into "it cannot happen".
// The bound also has to be honest about loops: an unwind bound that truncated
// any loop demotes I back to U, because with --solidity-max-tx 0 the dispatcher
// is a `while(nondet)` loop and --unwind therefore caps the transaction depth.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function f(uint256 a) public {
        if (a > 10) {
            if (a < 5) {
                x = 1;
            } else {
                x = 2;
            }
        } else {
            x = 3;
        }
    }
}
