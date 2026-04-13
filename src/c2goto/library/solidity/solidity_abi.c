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
