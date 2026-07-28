// A `require` inside an INTERNAL LIBRARY used to be lowered to a
// control-flow-free `assume`: the reverting execution did not exist in the
// model at all. That was the worst defect found in this pipeline, because it
// did not stop at "the decision set is incomplete":
//
//   1. only paths satisfying the guard were enumerated; the failing one was not
//      a sibling, because it was not a path;
//   2. the stage-2 bound is a syntactic product of intervals and cannot carry
//      the guard, so failing inputs sat inside it;
//   3. the stage-3 subtraction removes only enumerated siblings, so those
//      inputs were never removed;
//   4. the certified region therefore contained inputs that revert on-chain;
//   5. the stage-3 assertion query ran under the same `assume`, so the verifier
//      certified a candidate over inputs it had never seen.
//
// The emitted test would then revert on the UNMODIFIED contract while carrying
// a certified label — the same failure class as `this.f(...)`, where the model
// and the EVM disagree. And `require` inside a library is the standard shape
// (OpenZeppelin SafeERC20, every SafeMath variant), not a corner case.
//
// FIXED by widening the revert-observation scope: libraries and free functions
// are now observable scopes, so their `require` lowers to a real branch
// (`if (!c) { mark_revert(); return nondet; }`). Constructors and
// event/error definitions stay excluded — a constructor revert must prune,
// because the EVM aborts contract creation.
//
// This test pins the FIX, and pins it on all four counts, because a tripwire
// that only checks "the obstacle warning is gone" would pass for a half-fix in
// which the assume disappeared but no branch was generated:
//
//   * 3 paths, not 2 — the library's revert path really entered the enumeration
//   * all 3 are F, each with a counterexample
//   * revert exits go from 1 to 2 — the new path is classified as a revert
//   * no NAMED OBSTACLE warning — the unit is usable again
//
// The remaining `undetermined 1` is the unrelated, still-open issue that a
// value-returning function's normal exit has no positive evidence (the epilogue
// is emitted after the RETURN); see solidity_path_cov_return_exit_not_normal.
pragma solidity ^0.8.0;

library L2 {
    function g(uint256 a) internal pure returns (uint256) {
        require(a < 10);
        return a + 1;
    }
}

contract S2 {
    uint256 public x;

    function f(uint256 a) public returns (uint256) {
        uint256 r = L2.g(a);
        x = r;
        return r;
    }
}
