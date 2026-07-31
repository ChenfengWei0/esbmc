// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// THE bytesN PAYLOAD DEFECT, ON ITS OWN. Reduced out of P26_TypeMatrix, which
// carries seven parameter types and cannot say which of them is implicated.
//
// One unit, one parameter, one decision, and the other operand is a CONSTANT --
// so each path pins `b` COMPLETELY and the report can be checked against the
// source with no further information:
//
//     tag = 11  =>  the comparison was TRUE   =>  b MUST be 0x00..01
//     tag = 12  =>  the comparison was FALSE  =>  b != 0x00..01
//
// MEASURED on P26_TypeMatrix, the contract this was reduced from:
//
//     takeBytes32:path:6  final tag = 11
//                         inputs b = { .data={255 x32}, .length=0xFFFFFFFFFFFFFFFF }
//     takeBytes32:path:7  final tag = 12
//                         inputs b = IDENTICAL
//
// path:6's payload is DEFINITIVELY WRONG -- its `b` can only be `bytes32(1)`.
// This is not a missing coordinate and not a rendering gap: it is a wrong value.
//
// THE SHAPE NAMES THE SOURCE. A `bytes32` is FIXED length and must not carry a
// `.length` field at all; `.length = 0xFFFFFFFFFFFFFFFF` is an unconstrained
// nondet belonging to the DYNAMIC bytes wrapper, not the static one. That is
// also why both paths get the identical blob: it is not constrained by either
// path.
//
// WHAT THIS RULES OUT, each by measurement rather than by reading:
//   * the renderer -- it emits one call because it is handed one value;
//     `format_sol_value`, `is_bytes_wrapper_struct` and `format_fixed_bytes` are
//     all downstream of the wrong value;
//   * `inputs` first-wins -- changing it to last-write-wins, exactly as the
//     environment was changed, left P26 BYTE-IDENTICAL (still 2 merged cases,
//     still 6 skipped decisions), so the parameter is assigned once and the
//     reported value is the only one there is. That change was reverted.
//
// EXPECTED once fixed: 3 paths, and the two body paths carry DIFFERENT `b`, the
// tag-11 one being 0x00..01. Today they carry the same value and the emitter
// therefore renders one call for both, labelling it with both path ids.
//
// The two-way pin for any fix: `ce_consistency.py` currently REFUSES these
// decisions (it cannot parse `(_Bool)return_value$_bytes_static_equal$N`), so a
// correct fix must move them out of `skipped` WITHOUT producing a DISAGREE, and
// this contract's merged-case count must go to zero.
contract D11_Bytes32Equality {
    uint256 public tag;

    function takeBytes32(bytes32 b) external {
        if (b == bytes32(uint256(1))) {
            tag = 11;
        } else {
            tag = 12;
        }
    }
}
