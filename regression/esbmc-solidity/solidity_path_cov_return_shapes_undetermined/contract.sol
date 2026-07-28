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
