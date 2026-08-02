// ONE CLAIM KEY, DECIDED ONCE PER TRANSACTION.
//
// `take`'s successful branch is guarded by state only `put` can establish, and
// one transaction is exactly one entry call. So at --solidity-max-tx 2 the same
// assert instruction is reached twice -- once per unrolled transaction -- and
// the two solves DISAGREE: it holds in transaction 1 (nothing has written the
// map yet) and is refuted in transaction 2. The path's answer is the
// disjunction, which is the later one.
//
// That is expected and must NOT be an error: refusing it would refuse every
// multi-transaction run for doing the one thing a multi-transaction harness
// exists to do. What was missing is that nothing recorded it. `Verdicts
// Preserved` watches only the other direction -- a decision replaced by a
// NON-decision -- so it read 0 on exactly this run while a decision was being
// superseded.
//
// THE PAIR:
//   * this directory   tx=2 -> `1 extra solve(s)`, worst key decided 2x,
//                      ceiling 2, `1 decided verdict(s) superseded`, and the
//                      run completes.
//   * solidity_path_cov_claim_multiplicity_refused
//                      the same run with the ceiling forced to 1 -> ABORT
//                      naming the key. Without that direction the check has
//                      never been shown to fire and is indistinguishable from
//                      one that cannot.
pragma solidity ^0.8.0;

contract MultKey {
    mapping(uint256 => uint256) public bal;

    function put(uint256 k, uint256 v) external {
        require(v > 0);
        bal[k] = v;
    }

    function take(uint256 k, uint256 v) external {
        require(v > 0);
        require(bal[k] >= v);
        bal[k] -= v;
    }
}
