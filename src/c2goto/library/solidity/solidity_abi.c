/* [APPROX: OVER + UNDER] Solidity ABI encode/decode.
 *
 * abi.encode / abi.encodePacked / abi.encodeWithSelector /
 * abi.encodeWithSignature / abi.encodeCall are modelled as identity on
 * the first argument (cast to uint256_t). Remaining arguments are
 * evaluated for side effects and discarded. This is:
 *  - OVER-approx for equality reasoning: determinism + injectivity give
 *    `encode(x) == encode(x)` and `encode(a) == encode(b) ⇒ a == b`.
 *  - UNDER-approx for properties of the packed byte layout: two distinct
 *    encodings with identical first argument (e.g. different selectors
 *    or different trailing args) look the same to the model.
 *
 * abi.decode returns a nondet uint256_t — fully unconstrained.
 *  - OVER-approx: admits every possible decoded value, so any concrete
 *    buggy decode outcome is explored.
 *  - UNDER-approx for round-trip properties: the link between the bytes
 *    produced by abi.encode and the result of a later abi.decode is lost.
 *
 * False positives: properties that depend on the exact packed byte layout
 *   of multi-argument calls will not hold.
 * False negatives: properties that rely on the identity/uniqueness of a
 *   multi-argument encoding (e.g. function-selector dispatch) may report
 *   spurious success.
 */

#include <stdint.h>
#include "solidity_types.h"

/* ── abi.encode(...)  ────────────────────────────────────────────────── */
uint256_t abi_encode(uint256_t x)
{
__ESBMC_HIDE:;
  return x;
}

/* ── abi.encodePacked(...)  ──────────────────────────────────────────── */
uint256_t abi_encodePacked(uint256_t x)
{
__ESBMC_HIDE:;
  return x;
}

/* ── abi.encodeWithSelector(bytes4 selector, ...)  ───────────────────── */
uint256_t abi_encodeWithSelector(uint256_t x)
{
__ESBMC_HIDE:;
  return x;
}

/* ── abi.encodeWithSignature(string memory signature, ...)  ──────────── */
uint256_t abi_encodeWithSignature(uint256_t x)
{
__ESBMC_HIDE:;
  return x;
}

/* ── abi.encodeCall(function, (...))  ────────────────────────────────── */
uint256_t abi_encodeCall(uint256_t x)
{
__ESBMC_HIDE:;
  return x;
}

/* ── abi.decode(bytes memory, (types))  ──────────────────────────────── *
 *
 * In real Solidity, abi.decode unpacks ABI-encoded bytes into typed values.
 * We model it as returning a nondet uint256 — an over-approximation that
 * is sound for safety properties.  The caller cannot assume any specific
 * decoded value, so no real bug is masked.
 *
 * When the frontend encounters abi.decode with a tuple return, each
 * component is independently nondet.
 */
uint256_t abi_decode(uint256_t x)
{
__ESBMC_HIDE:;
  uint256_t result;
  return result;
}

/* ── multi-arg fold helper (T2.1)  ──────────────────────────────────── *
 *
 * Combine an accumulator with the next ABI-encoded argument so the
 * frontend's abi.encode/encodePacked/encodeWithSelector/
 * encodeWithSignature/encodeCall lowering can chain N args into a
 * single uint256 result.  Form:
 *
 *   acc' = acc * 0x100000001b3 + next
 *
 * **Soundness:** this fold is **NOT injective under SMT**.  Multiplication
 * mod 2^256 is not a permutation under wraparound; for any (acc1, n1)
 * the solver can find (acc2, n2) ≠ (acc1, n1) with equal output.  The
 * earlier "FNV-injective" comment was formally void in a BMC/SMT context
 * where the solver actively searches adversarial assignments.  The
 * resulting under-approximation is regression-locked under
 * `abi_fold_collision_distinct_pass_knownbug` and ledger entry #3 (open).
 *
 * Closure requires a sound bit-vector tuple encoding (e.g. position-
 * tagged concatenation followed by a true bijection).  Until then, this
 * helper remains the practical-but-unsound fold; consumers should
 * understand that `keccak256(abi.encode(a, b)) == keccak256(abi.encode(c, d))`
 * may report TRUE for distinct argument tuples.
 *
 * Solver-friendly: a single MUL + ADD per step, no shifts or array
 * writes — keeps the encoding lightweight while we wait for the
 * architectural fix.
 */
uint256_t _ESBMC_abi_fold(uint256_t acc, uint256_t next)
{
__ESBMC_HIDE:;
  return acc * (uint256_t)0x100000001b3ULL + next;
}
