// Paired with solidity_path_cov_maxtx0_is_shallower — SAME contract, only the
// transaction bound differs.
//
//   solidity_path_cov_maxtx0_is_shallower (--solidity-max-tx 0) -> Reached : 2
//   this test                             (--solidity-max-tx 2) -> Reached : 3
//
// `fire()`'s `armed` branch needs a prior `arm()` transaction. Reaching it at
// max_tx 2 and NOT at max_tx 0 is the measurement that refutes "max_tx 0 is an
// unbounded run": the flag that reads as unbounded explores strictly less.
//
// The practical rule this pins for callers: to explore deeper, raise
// --solidity-max-tx N. Never set it to 0 expecting more.
pragma solidity ^0.8.0;

contract S {
    uint256 public x;
    bool public armed;

    function arm() public {
        armed = true;
    }

    function fire() public {
        if (armed) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
