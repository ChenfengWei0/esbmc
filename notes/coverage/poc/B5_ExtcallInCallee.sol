// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ⛔ EVERY COMMENT IN THIS FILE IS `//` AND NOT `///`, on purpose. Several of
// them quote mangled symbol ids, which contain `@`, and solc's NatSpec parser
// reads that as a documentation tag inside a `///` comment: the compile fails
// and leaves a ZERO-BYTE .solast. That has already happened once in this
// directory and had to be restored from git.
//
// ISOLATES ONE THING: WHICH FRAME the assembly block sits in.
//
// WHERE THE QUESTION COMES FROM, measured, both sides on disk. A fourth bucket
// was added to the counterexample classifier so that a nondet-sourced LOCAL of
// the unit under test lands in `extcall_returns` instead of being dropped for
// want of a bucket. On the twelve-line isolation it fires exactly as intended:
//
//     B2_ExtcallSuccess.probe   (assembly INSIDE the unit's own body)
//       path 6  extcall_returns = [{"symbol": "ok", "value": "1"}]  dropped 24 -> 23
//       path 7  extcall_returns = [{"symbol": "ok", "value": "0"}]  dropped 24 -> 23
//       path 2  extcall_returns = []                                dropped 23 (never assigns ok)
//     B2_ExtcallSuccess.ctrlBool (`ok` is a real PARAMETER)
//       all paths: `ok` stays in `inputs`, extcall_returns = []
//
// On farming/deposit the same binary harvests NOTHING, and the tool's own
// counter says the values are still being dropped there -- 197 and 199 on the
// two claims of the certification round, with an empty list beside them. So the
// value exists and is discarded; it is not absent.
//
// THE ONE STRUCTURAL DIFFERENCE between the two: in B2 the assembly is written
// in the unit's own body, while deposit's is inside `SafeERC20.safeTransferFrom`
// -- a LIBRARY function. A callee's local carries the callee's scope in its
// mangled id, and the new bucket only takes symbols whose id starts with THIS
// path's own function scope. That scope test is not decoration: it is what keeps
// dispatcher and harness locals out of the payload. So it cannot simply be
// deleted, and before touching it the frame has to be shown to be the cause.
//
// THE TWO UNITS DIFFER IN EXACTLY THAT AND NOTHING ELSE. Same call, same
// success bit, same `if (!success) revert`, same single state write. The only
// difference is whether the assembly block is written here or one frame down.
//
// ⛔ THE VOID RETURN IS DELIBERATE and it is what makes this deposit's shape
// rather than a third one. `SafeERC20.safeTransferFrom` returns NOTHING: it
// reverts internally on failure, so the calling unit has no local of its own
// bound to the result. A library returning `bool` into `bool ok = lib.f()` would
// create a local in the UNIT's scope and the new bucket would take it for that
// reason alone -- which would answer a question nobody asked.
//
// EXPECTED, written before the run:
//
//   probeInline  POSITIVE CONTROL. Two complete paths splitting on the success
//                bit, and the bit must appear in `extcall_returns` -- this is
//                B2's `probe` plus a revert. If it does NOT appear, the revert
//                form is a second factor, this file is the wrong isolation, and
//                nothing may be concluded about the frame.
//
//   probeLib     THE QUESTION.
//                (a) empty while probeInline is populated -> THE CALLEE FRAME is
//                    the cause. The repair is to decide membership by the CALL
//                    STACK (is this frame below the unit under test?) instead of
//                    by a name prefix, which is a different test with the same
//                    purpose and keeps dispatcher locals out.
//                (b) populated -> the frame is EXCLUDED, and deposit's 197
//                    dropped values need a different explanation. The next place
//                    to look is then the library being `internal` and inlined
//                    versus the call being a real frame at all.
//
// ⛔ NEITHER OUTCOME IS "the fix works". Harvesting the bit is not the same as
// certifying the paths: B2's own third control measured that a bool coordinate
// comes out as a POINT in each region, so a test built on it is concrete on that
// coordinate while the others stay ranged. That is a gain and it is smaller than
// full generalisation, and it must be reported as the smaller thing.

library B5Lib {
    error CallFailed();

    // The shape of SafeERC20.safeTransferFrom, reduced to the part that matters:
    // a low-level call in an assembly block, the success bit branched on, and a
    // revert on failure. Returns nothing, exactly as the original does.
    function mustCall(address token) internal {
        bool success;
        assembly ("memory-safe") {
            success := call(gas(), token, 0, 0, 0, 0, 0)
        }
        if (!success) revert CallFailed();
    }
}

contract B5_ExtcallInCallee {
    error CallFailedHere();

    uint256 public tag;

    // THE QUESTION: the assembly block is one frame down.
    function probeLib(address token, uint256 amount) external {
        B5Lib.mustCall(token);
        tag = amount + 1;
    }

    // POSITIVE CONTROL: byte-for-byte the same work, written in this frame.
    function probeInline(address token, uint256 amount) external {
        bool success;
        assembly ("memory-safe") {
            success := call(gas(), token, 0, 0, 0, 0, 0)
        }
        if (!success) revert CallFailedHere();
        tag = amount + 1;
    }
}
