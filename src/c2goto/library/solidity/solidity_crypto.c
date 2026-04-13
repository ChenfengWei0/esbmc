/* [APPROX: OVER + UNDER] Solidity crypto hashes — deterministic
 * bijective identity abstraction. Each hash is a simple bitwise xor:
 *   keccak256(x) = ~x, sha256(x) = ~(x+1), ripemd160(x) = ~(x+2),
 *   ecrecover(h,v,r,s) = ~h.
 *
 * Modeling properties:
 *  - Functional consistency (same input → same output): keccak256(x) ==
 *    keccak256(x) is provable.
 *  - Injectivity (different inputs → different outputs): keccak256(a) ==
 *    keccak256(b) iff a == b. This is OVER-approximate relative to a real
 *    hash (no collisions exist here that a real hash would expose, but the
 *    frontend never relies on finding a collision so it is sound).
 *  - Distinct hash families: keccak256(x) != sha256(x) for x != 0.
 *  - Concrete hash values: NOT computed. Any property of the form
 *    `keccak256(0) == 0xc5d2...` is UNPROVABLE — UNDER-approximate for
 *    reasoning that depends on the real bit pattern of a specific hash.
 *  - ecrecover: returns a deterministic function of `hash` only, ignoring
 *    (v,r,s). An attacker model that requires signature forgery is not
 *    captured — UNDER-approximate for auth-bypass properties.
 *
 * Use cases covered:
 *  ✓ Equality-based reasoning (set membership via hashed keys)
 *  ✓ Uniqueness of derived IDs
 * Use cases NOT covered:
 *  ✗ Specific hash bit patterns (preimage witness, checksum matching)
 *  ✗ Signature-verification semantics of ecrecover
 */
#include <stddef.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include "solidity_types.h"

uint256_t keccak256(uint256_t x)
{
__ESBMC_HIDE:;
  return ~x;
}

uint256_t sha256(uint256_t x)
{
__ESBMC_HIDE:;
  return ~(x + 1);
}

address_t ripemd160(uint256_t x)
{
__ESBMC_HIDE:;
  return (address_t)(~(x + 2));
}

address_t ecrecover(uint256_t hash, unsigned int v, uint256_t r, uint256_t s)
{
__ESBMC_HIDE:;
  return (address_t)(~hash);
}
