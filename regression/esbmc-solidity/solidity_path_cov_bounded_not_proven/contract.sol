// `--solidity-max-tx 0` is NOT a proof, and this test exists to stop anyone
// (including a previous version of this very test) believing that it is.
//
// The path `a > 10 && a < 5` is genuinely unreachable. It is still reported
// **U**, never I, because no coverage configuration can establish
// unreachability:
//
//   * `--solidity-max-tx N` with N > 0 emits N straight-line transactions —
//     bounded by construction.
//   * `--solidity-max-tx 0` asks the frontend for the
//     `while (nondet_bool()) dispatch()` driver, so it READS as unbounded. But
//     that loop is then destroyed: process_goto_program calls make_skip() on
//     every backwards goto in each `_ESBMC_Main*` function whenever coverage is
//     on and --coverage-multi-tx was not given. One guarded transaction is
//     left, so it is the SHALLOWEST setting, not an unbounded one.
//     Cross-checked without relying on documentation: `--show-loops` lists
//     `_ESBMC_Main_*` as a loop without the coverage flag and omits it with the
//     flag; and the paired test solidity_path_cov_maxtx0_is_shallower measures
//     a path that --solidity-max-tx 2 reaches and --solidity-max-tx 0 does not.
//   * the entry state is whatever the constructor left; state variables are
//     never havoc'd, so an UNSAT only says "not reachable from THIS entry
//     state".
//
// An earlier version of this test pinned `F 2, I 1, U 0` here, on the belief
// that max_tx 0 gave an unbounded run. That pinned a FALSE PROOF: a
// one-transaction budget reported as "cannot happen". The expected value is
// therefore `F 2, I 0, U 1`, identical to the same contract under
// `--solidity-max-tx 1` (solidity_path_cov_infeasible) — which is exactly the
// point: max_tx 0 buys no extra exploration, so it must not buy a stronger
// verdict.
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
