// Pins the MEASURED refutation of "a rolled-back execution never reaches a
// RETURN", and with it the current ceiling on exit classification.
//
// Had the proposition held, reaching a RETURN would have been positive evidence
// of a normal exit — evidence independent of the revert-observation gate and of
// which scopes that gate covers. It does not hold: when the enclosing function
// returns a value, the frontend lowers a failing `require` to
//     { *this = _sol_save_this; return [nondet]; }
// so the reverting execution ends at a RETURN of the frontend's own making.
// Measured on this exact contract, and again on a modifier's require (which had
// been predicted to be a positive case, and is one only when the function
// returns nothing).
//
// So the inference "ends at a RETURN, therefore normal" is not available, and
// the normal exit here has no positive evidence at all: the function epilogue
// that would supply it sits AFTER the RETURN, unreachable on that path.
// Expected therefore:
//
//   normal 0, revert 2, undetermined 1
//     revert 2      -- the ABI value-reject path and the require-failure path
//     undetermined 1 -- `return r`, a perfectly ordinary exit that cannot be
//                       shown to be one
//
// UPDATE — the frontend contract landed and this test went red exactly as it
// was designed to. It now pins `normal 1, revert 2, undetermined 0`.
//
// What changed: the frontend stamps `sol_source_return` on the RETURNs it
// lowers from a source-level `return`, and NOT on the one it synthesises for a
// failing `require` (`{ *this = _sol_save_this; return [nondet]; }`). That is
// positive evidence, supplied by the only party that can tell the two apart,
// and it is what the epilogue could never supply here — the epilogue is emitted
// after the RETURN, so a returning path never reaches it.
//
// The refutation this contract was built around still stands and is still the
// reason the marker is needed: a rolled-back execution DOES reach a RETURN, so
// "ends at a RETURN" is not evidence of anything by itself. The rollback test
// therefore still runs first and still wins; only a path with no rollback
// evidence AND an affirmative source-return marker is called normal.
//
// `undetermined 0` is now the number that matters, and `revert 2` guards the
// other direction: if the marker ever leaked onto the synthesised RETURN, the
// require-failure path would be reported normal and this line would read
// `normal 2, revert 1`. Both halves are pinned, so the test is decisive in both
// directions.
//
// Unblocks R0 (the zero-query oracle tier) on value-returning functions —
// getters and views, the most common shape in real contracts.
pragma solidity ^0.8.0;

contract S1 {
    uint256 public x;

    function f(uint256 a) public returns (uint256) {
        uint256 r = a + 1;
        x = r;
        require(a < 10);
        return r;
    }
}
