// A UNIT'S BODY HAS TWO IDENTITIES, and leaving a call to one of them
// unexpanded picks the wrong one.
//
// The copy of a unit expanded into a caller models an INTERNAL call and is
// gate-free. The unit's OWN body models an EXTERNAL entry and carries the
// synthesised ABI non-payable gate:
//
//     IF msg_value == 0 THEN GOTO body; mark_revert(); GOTO END_FUNCTION
//
// Physical expansion exists precisely to give the two entry kinds separate code.
// So any mechanism that leaves an internal call pointing at the unit's own body
// re-opens the hole: the model then admits "the callee reverted because the
// transaction carried value" inside a caller that on-chain proceeds normally.
// That execution does not exist on chain, and a test built from a counterexample
// containing it is RED on the unmodified contract — the one outcome this
// pipeline must never produce.
//
// Degradation refuses to withdraw such a call point for exactly this reason
// (solidity_path_cov_degradation_keeps_unit_callee). But the CALL DEPTH BOUND
// can leave one unexpanded without asking anyone, which is the same hole reached
// from the other side — and until this test existed it was only WARNED about,
// not contained. A warning that does not stop the paths reaching a downstream
// emitter is a known red-test channel left open.
//
// The chain a -> b -> c is three units deep, so at `--unwind 1` one expansion
// pass reaches `b` but the call to `c` that `b` brought in stays a call, and `c`
// is public. Expected:
//
//   a -- 5 paths (gate + 2 own decisions x b's decision), ALL of them named
//        obstacles: it is `a` that holds the residual call
//   b -- 4 paths (gate + own decision x c's decision), clean: its call to `c`
//        was expanded in the same pass
//   c -- 4? no: 3 (gate + own decision), clean
//   total 13 across 3 units, obstacles (a) 0 / (b) 5 across 1 unit
//
// The `(a) 0 / (b) 5` split is the sharp pin: it separates this failure from the
// branch-free-assume one, which is the same failure by a different route and must
// stay separately counted.
//
// It also pins the U REASON TOKENS, and specifically their PRIORITY. `a:path:14`
// is both a named obstacle and a path the solver found no witness for at this
// bound, so both `named-obstacle` and `bounded-holds` would apply. The obstacle
// wins, because it is not an unknown that better solving could resolve — no
// verdict can put a disqualified unit's path back in play. The full five-slot
// line is pinned, zeros included, so a category that stops being emitted goes
// red instead of quietly vanishing from the report.
//
// PAIRED with solidity_path_cov_residual_unit_call_expanded: the SAME contract at
// `--unwind 3` expands the whole chain, `a` grows to 9 paths, the total is 17 and
// NO obstacle is reported at all. Together the two pin both directions — if the
// containment ever stops firing this test goes green with zero obstacles (the
// empty-check failure), and if it ever over-fires the paired test goes red.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function a(uint256 v) public {
        if (v > 1) {
            x = 1;
        }
        b(v);
    }

    function b(uint256 v) public {
        if (v > 2) {
            x = 2;
        }
        c(v);
    }

    function c(uint256 v) public {
        if (v > 3) {
            x = 3;
        }
    }
}
