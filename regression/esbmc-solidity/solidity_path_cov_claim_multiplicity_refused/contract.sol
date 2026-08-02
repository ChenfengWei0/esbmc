// The REFUSAL half of the claim-multiplicity pair (see
// solidity_path_cov_claim_multiplicity_counted for the positive one and for
// why repetition up to the ceiling is expected rather than wrong).
//
// A ceiling derived from the run's own bounds is never reached by a sound
// instrumentation, so a check armed only from those bounds could never be
// SHOWN to fire -- and this pass has already shipped a guard that was always
// true and a function that was never called. `--path-cov-max-claim-solves`
// exists for exactly this: it forces the ceiling low enough that the ordinary,
// legitimate second solve trips it, so the refusal executes on a real run with
// a real key in the message.
//
// The contract is identical to the positive test's, so the two differ in
// exactly one thing -- the forced ceiling -- and this test cannot pass because
// of some other property of the source.
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
