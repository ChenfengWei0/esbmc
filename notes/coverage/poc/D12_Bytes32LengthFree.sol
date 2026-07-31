// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// AN ABI bytesN ARGUMENT'S `.length` IS NOT PINNED, AND THAT CAN SHIP A RED
// TEST. Found while fixing D11_Bytes32Equality, and deliberately kept separate
// from it: D11 was a statement-ordering bug in the frontend, this is a missing
// constraint on the parameter itself. Fixing the first did not touch the second
// and the two are not variants of one thing.
//
// bytesN is modelled as `BytesStatic { unsigned char data[32]; size_t length; }`
// (solidity_bytes.c:16-19) -- the `.length` is legitimate, because N ranges over
// [1,32]. `bytes_static_equal` returns FALSE OUTRIGHT when the two lengths
// differ (solidity_bytes.c:354), before comparing a single byte.
//
// The harness builds a public function's arguments in `assign_param_nondet`
// (solidity_convert_call.cpp). bytesN falls into the branch whose comment calls
// it a "Scalar harness parameter (uint/int/bool/address/bytesN/enum)" and hands
// it a bare `sideeffect(nondet)` of the struct type -- which leaves `.length`
// as free as `.data`. On the chain an ABI `bytes32` argument has length 32 by
// construction; there is no calldata encoding of a bytes32 with any other
// length.
//
// MEASURED -- each of these is a tautology on the chain, and ESBMC reports
// VERIFICATION FAILED for two of them:
//
//     b == bytes32(uint256(b))                        FAILED
//     !(b == bytes32(uint256(1))) && uint256(b) == 1   FAILED (reachable)
//     x==y && y==z && !(x==z)                          SUCCESSFUL
//
// The third passing is not evidence of soundness: transitivity survives a free
// length because all three operands are equally free. The first two isolate the
// real asymmetry -- a PARAMETER with free length against a CONSTANT built by
// `bytes_static_from_uint(K, 32)`, which pins length to 32.
//
// WHY THIS IS A RED TEST AND NOT JUST AN IMPRECISION. The not-equal arm becomes
// reachable for a reason the chain cannot reproduce: same 32 data bytes, wrong
// length. The emitter renders the argument from `.data` alone, as
// `bytes32(0x...)`, and on the chain that value HAS length 32 -- so the
// comparison flips, the call walks the other arm, and the test asserting this
// path's exit fails on the very contract it was generated from.
//
// THE FIX WAS WRITTEN, MEASURED, AND REVERTED -- recorded here so the next
// attempt starts from what is already known rather than repeating it.
//
// Passing `bytes_static_from_uint(nondet_uint256(), N)` as the harness argument
// pins `.length` and makes BOTH tautologies SUCCESSFUL. It also routes around
// `get_nondet_expr`, and the comment above that branch in
// solidity_convert_call.cpp already says why that is not free: only the
// `nondet$symex::` symbol it mints is visible to the counterexample harvest.
// MEASURED with the change in: the parameter came back DEFAULTED
// (`defaulted_types=BYTES32 x3`), all three paths of D11_Bytes32Equality
// rendered the same call, and the funnel went
//
//     3 paths / 3 cases / 0 merged   ->   3 paths / 1 case / 1 merged
//
// A sound model whose counterexample cannot be read back is not an improvement
// for a pipeline whose deliverable is the test. So the soundness gap is left
// open and PINNED (`solidity_bytesn_param_length_free_knownbug`) instead of
// being traded for a recoverability gap.
//
// EXPECTED ONCE PROPERLY FIXED: `roundTrip` and `lengthGap` both SUCCESSFUL,
// `reachable` still able to reach tag = 3, AND the funnel numbers on
// D11_Bytes32Equality unchanged at 3 cases / 0 merged. All four, or it is the
// same trade again.
contract D12_Bytes32LengthFree {
    uint256 public tag;

    // Chain tautology: converting to uint256 and back is the identity on a
    // bytes32. The model can refute it.
    function roundTrip(bytes32 b) external {
        if (!(b == bytes32(uint256(b)))) {
            tag = 1;
        }
    }

    // Chain-impossible conjunction: `uint256(b) == 1` means the 32 bytes ARE
    // 0x00..01, which is exactly `bytes32(uint256(1))`.
    function lengthGap(bytes32 b) external {
        if (!(b == bytes32(uint256(1))) && uint256(b) == 1) {
            tag = 2;
        }
    }

    // VACUITY GUARD: this arm must stay reachable, so a "fix" that made every
    // bytesN comparison false is not mistaken for a repair.
    function reachable(bytes32 b) external {
        if (b == bytes32(uint256(1))) {
            tag = 3;
        }
    }
}
