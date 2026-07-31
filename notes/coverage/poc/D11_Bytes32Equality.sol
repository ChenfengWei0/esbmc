// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// THE bytesN PAYLOAD DEFECT, ON ITS OWN -- AND ITS ROOT CAUSE, WHICH IS NOT
// WHERE THIS FILE ORIGINALLY SAID IT WAS. Reduced out of P26_TypeMatrix, which
// carries seven parameter types and cannot say which of them is implicated.
//
// One unit, one parameter, one decision, and the other operand is a CONSTANT --
// so each path pins `b` COMPLETELY and the report can be checked against the
// source with no further information:
//
//     tag = 11  =>  the comparison was TRUE   =>  b MUST be 0x00..01
//     tag = 12  =>  the comparison was FALSE  =>  b != 0x00..01
//
// SYMPTOM AS MEASURED (before the fix): both paths reported the IDENTICAL
// `b = { .data={255 x32}, .length=0xFFFFFFFFFFFFFFFF }`, so path:6's payload
// was definitively wrong -- its `b` can only be bytes32(1) -- and the emitter
// rendered ONE call labelled with BOTH path ids. Worse than a missing test: the
// call takes the else arm, reaches path:7 alone, and still passes, so a wrong
// path attribution shipped as a GREEN test that no later stage can notice.
//
// ROOT CAUSE, found by asking whether the equality constrains `b` AT ALL. It
// does not: ESBMC reported VERIFICATION FAILED for
//
//     if (b == bytes32(uint256(1)) && b == bytes32(uint256(2))) tag = 1;
//     assert(tag != 1);
//
// i.e. the model admitted one bytes32 equal to two different constants. The
// goto program says why -- it emitted `bytes_static_equal(&b, &_ESBMC_aux18)`
// and only AFTERWARDS `DECL _ESBMC_aux18; _ESBMC_aux18 =
// bytes_static_from_uint(1, 32)`. The comparison read the temporary before it
// was built, so it compared against an unconstrained struct.
//
// The ordering came from a shared pending queue with two readers that disagreed
// about ownership: `flush_pending_into_body` deliberately leaves entries queued
// before a body alone, while `get_block` drained the queue whole. For a BRACED
// body get_block ran first and swallowed the condition's temporaries into the
// body. The brace-less spelling of the same source was correct all along, which
// is exactly why this survived -- two syntactic positions of one construct
// disagreed and only one was covered.
//
// THE FIRST FIX WAS TOO BROAD, AND THE REGRESSION SET CAUGHT IT. Lifting the
// WHOLE pending queue above the branch also lifted statements that must stay
// under a guard: `k < 2 && b[k] == 0` queues the bounds assertion for `b[k]`,
// which the chain evaluates only when `k < 2`. Unconditional, it reports a
// bounds violation for k >= 2 that no execution performs -- a false positive,
// which in this pipeline is a RED generated test. It broke
// `local_array_bounds_shortcircuit_guard_pass`, i.e. one soundness hole traded
// for another.
//
// The two cannot be separated by statement kind without guessing. They ARE
// separated by a property of the data: an operand temporary exists BECAUSE the
// condition reads it (the condition literally contains `&_ESBMC_aux18`), while
// the bounds assertion declares nothing the condition mentions. So only the
// pending statements whose declared symbol appears in the converted condition
// are lifted, and everything else keeps its previous placement exactly.
// `hoist_operands_read_by` in solidity_convert_stmt.cpp; regressions
// solidity_braced_body_cond_aux_{if,while,reachable}, with the short-circuit
// test as the paired must-not-break.
//
// TWO EARLIER EXPLANATIONS, BOTH WRONG, KEPT SO THEY ARE NOT RE-PROPOSED:
//   * "the counterexample harvest reports the wrong value" -- no; the harvest
//     reported the model faithfully. The model was wrong.
//   * "a bytes32 must not carry a `.length` field at all, so the value came
//     from the DYNAMIC bytes wrapper" -- no; `BytesStatic` legitimately has a
//     `.length` field (solidity_bytes.c:16-19), because bytesN is N in [1,32].
//     The all-ones blob was an unconstrained struct, not the wrong struct.
//
// MEASURED AFTER THE FIX -- the two body paths now carry DIFFERENT `b`, and the
// tag-11 one is exactly bytes32(1):
//
//     takeBytes32:path:6  tag=11  b = { .data={0 x31, 1}, .length=32 }
//     takeBytes32:path:7  tag=12  b = { .data={255 x32}, .length=0xFFFFFFFFFFFFFFFF }
//
// and this contract's merged-case count went 2 -> 0 (3 paths, 3 cases, 3 GREEN).
//
// ONE THING THE FIX DID NOT ADDRESS, recorded rather than left implicit:
// path:7's `.length` is still unconstrained. An ABI `bytes32` argument has
// length 32 by construction, so a free `.length` lets the model take the
// not-equal arm for a reason the chain cannot reproduce. It does not misfire
// here (the data differs too), but it is a separate open question about the
// parameter's own constraints, not about statement ordering.
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
