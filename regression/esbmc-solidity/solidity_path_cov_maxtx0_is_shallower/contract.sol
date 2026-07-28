// MEASURES the fact that the tri-state rule depends on: in Solidity coverage
// mode `--solidity-max-tx 0` explores FEWER transactions than
// `--solidity-max-tx 2`, so it cannot be read as "unbounded".
//
// `fire()`'s `armed` branch is reachable only after a PRIOR `arm()`
// transaction, so the number of transactions explored is directly observable
// in the path count:
//
//   this test  (--solidity-max-tx 0) -> Reached : 2 of 3   (armed path missed)
//   the paired solidity_path_cov_maxtx2_reaches_more
//              (--solidity-max-tx 2) -> Reached : 3 of 3   (armed path hit)
//
// Why: max_tx 0 asks the frontend for the `while (nondet_bool()) dispatch()`
// driver, and process_goto_program then calls make_skip() on every backwards
// goto in each `_ESBMC_Main*` function, leaving one guarded transaction.
// max_tx 2 emits two straight-line transactions with no back-edge for that pass
// to remove. Independently visible with `--show-loops`: `_ESBMC_Main_S` is
// listed as a loop without the coverage flag and is absent with it.
//
// If this ever flips (Reached : 3 here), the "max_tx 0 == unbounded" belief
// would be true again and the I verdict could be re-enabled — but until then,
// promoting an UNSAT at max_tx 0 to "proven unreachable" reports a
// one-transaction budget as a proof.
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
