// DEGRADATION MUST NEVER WITHDRAW A CALL TO A UNIT, and the reason is
// soundness, not taste.
//
// Withdrawing a call point means leaving a direct call to the callee's own
// body. If that callee is a public/external function it is a UNIT, and its body
// carries the synthesised ABI non-payable gate:
//
//     IF msg_value == 0 THEN GOTO body; mark_revert(); GOTO END_FUNCTION
//
// which models an EXTERNAL entry. The call reaching it from inside another
// function is INTERNAL, and an internal call never runs that gate on-chain. So
// withdrawing such a call point lets the model admit "the callee reverted
// because the transaction carried value" in the middle of a caller that on-chain
// proceeds normally — a counterexample describing an execution that does not
// exist, i.e. a test that is RED on the unmodified contract. Physical expansion
// is precisely what fixes this (the caller's copy is gate-free while the
// callee's own body keeps its gate), so undoing it for a unit callee re-opens
// the hole that expansion was adopted to close.
//
// This test squeezes the budget to 2, which no unit here can meet, and pins that
// the choice made under that pressure is still the safe one:
//
//   * `caller` calls `pub` (public -> a UNIT -> NOT withdrawable) and `helper`
//     (private -> withdrawable). Degradation must therefore report exactly ONE
//     withdrawn call point, `helper`. If `pub` were ever offered as a candidate
//     the count would be 2 and the "STILL over the budget ... with every one of
//     its 1 call point(s) withdrawn" line would disappear — so the pinned "1" is
//     decisive in both directions.
//   * `pub` itself has no internal calls at all, so degradation has nothing to
//     offer it, and says so BEFORE the enumeration runs.
//
// Both units then hit the goal cap, and that is the second thing pinned here:
// truncation firing is reported with its CAUSE. "Degradation ran out of things
// to withdraw", "degradation was not aggressive enough" and "the estimator
// disagreed with the enumeration" all look identical at the cap, and only the
// last is a defect. The negative lookahead pins that the drift message — the
// defect one — is NOT what fires here.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function pub(uint256 a) public {
        if (a > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }

    function helper(uint256 a) private {
        if (a > 3) {
            x = 3;
        }
    }

    function caller(uint256 a) public {
        pub(a);
        helper(a);
    }
}
