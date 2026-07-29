// TRIPWIRE. This pins the CURRENT, WRONG behaviour and must go RED when the
// frontend is fixed. It is the minimal reproduction of the residue that
// survived the frontend-contract batch: after that batch every one of the
// other regressions reports `undetermined 0`, yet a real contract (Aqua) still
// reported 16. The grouping diagnostic named two shapes, and this contract is
// those two shapes and nothing else.
//
// Why the evidence is missing. A normal exit can only be established by
// POSITIVE evidence, and there are exactly two kinds:
//   * the function epilogue  — but the frontend emits it AFTER the exit, so a
//     path that leaves via the exit never walks it;
//   * `sol_source_return`    — stamped only on a RETURN lowered from a
//     source-level `return` STATEMENT.
// A normal exit that is neither a source-level `return` nor reaches the
// epilogue therefore has no witness at all. Two ordinary Solidity constructs
// produce exactly that, and neither appeared in any earlier regression, which
// is why they all read `undetermined 0` while Aqua did not:
//
//   f  named return value, NO `return` statement — the frontend synthesises
//      the RETURN, so there is no source `return` to stamp   [cause (3)]
//   h  explicit tuple `return (a, b);` — routed through tuple_instance and
//      emitted as a VALUELESS `return;`, i.e. a jump to END_FUNCTION that
//      goes over the epilogue                                [cause (2)]
//   g  named multi-return, no `return` statement — same jump [cause (2)]
//   k  explicit single-value `return <expr>;` — the shape every other
//      regression uses; it is the CONTROL and is reported `normal`.
//
// Measured on Aqua, same two shapes: `ship` (named single return, no `return`
// statement) contributed 10 of the 16, and `rawBalances` / `safeBalances`
// (tuple / named multi-return) the other 6.
//
// No function here branches, so each contributes exactly 2 paths — the ABI
// value-reject edge plus its own exit — giving 8 paths and `revert 4`. The
// control `k` supplies the `normal 1`; the three broken shapes supply the
// `undetermined 3`. Coverage is 100%, so a drop in `Reached` would mean
// something ELSE broke rather than this being fixed.
//
// AFTER THE FIX this line must read `normal 4, revert 4, undetermined 0` with
// the path count unchanged at 8. A fix that changes the path count has changed
// which decisions exist, not just which evidence is available, and is wrong.
//
// ── 2026-07-29 (later): BOTH CAUSES FIXED. The declared end state was hit. ──
//
// This file is no longer a tripwire. It now pins the state its own header
// predicted, and it landed on the predicted numbers exactly:
//
//     Path Exits: normal 4, revert 4, undetermined 0     with Complete Paths 8
//
// Both of its own guards held. The path count is unchanged at 8, so what
// changed is which evidence exists, not which decisions do -- the distinction
// this file was written to enforce. And `revert 4` never moved across either
// fix, so nothing was reclassified out of the reverting bucket to make the
// normal count rise.
//
// Cause (3), `f`: the frontend now stamps the synthesised RETURN of a
// named-return function with `sol_source_return`, since running to the closing
// brace is a normal exit.
//
// Cause (2), `h` and `g`: a body ending in a VALUELESS `return;` made the
// enclosing-contract restores unreachable, so goto conversion deleted them --
// and with them the only positive evidence of a normal exit. The restores are
// now emitted BEFORE that trailing return instead. Not by deleting the return:
// a valueless return elsewhere in a body is a jump and load-bearing. Straight-
// line reordering only, which is why the path count could not move.
//
// Keeping the record below rather than deleting it: the intermediate state was
// real, and the reason the item was split across two rounds is worth more than
// the tidiness of a single entry.
//
// ── 2026-07-29 (earlier): cause (3) IS FIXED. CAUSE (2) IS NOT. ─────────────
//
// The tripwire did its job and went red. `f` is now `normal`: the frontend
// stamps its synthesised RETURN with `sol_source_return`, because falling off
// the end of a named-return function is a normal exit. `h` and `g` are
// UNCHANGED and still undetermined, so the pinned line is now
// `normal 2, revert 4, undetermined 2` -- an INTERMEDIATE state, deliberately
// pinned so this file keeps working as a tripwire for what is left.
//
// The end state above is still the end state. Do not read the improvement as
// completion: on aqua this same change took `Aqua.ship` from 62 undetermined
// exits to 0 and the contract total from 68 to 6, which looks like the job is
// done and is not. `rawBalances` (2) and `safeBalances` (4) are untouched.
//
// What is left is ONE entry condition in the code, not two, even though it
// shows up as two shapes here. `h` (explicit tuple return) and `g` (named
// multi-return, no return statement) both end as a VALUELESS `code_returnt`,
// and goto conversion erases that -- either merging it into a preceding branch
// or dropping it as a fall-through -- so no instruction survives to carry a
// marker. Marking a RETURN cannot fix them because there is no RETURN left.
// The same erasure hits a plain `return;` in a void function, which is why the
// return-shape matrix regression carries that cell too.
//
// Note for whoever fixes it: the epilogue is NOT a witness that can be relied
// on here. It is the enclosing-contract save/restore, reused as evidence, and
// the frontend documents in solidity_convert_modifier.cpp that early returns
// deliberately skip the trailing restore because the stale value is harmless.
// A witness that the code is documented as free to skip is not a witness.
pragma solidity ^0.8.0;

contract RetShapes {
    function k(uint256 a) external pure returns (uint256) {
        return a;
    }

    function f(uint256 a) external pure returns (uint256 r) {
        r = a + 1;
    }

    function h(uint256 a) external pure returns (uint256, uint256) {
        return (a, a + 1);
    }

    function g(uint256 a) external pure returns (uint256 x, uint256 y) {
        x = a;
        y = a + 1;
    }
}
