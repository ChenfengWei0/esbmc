/* Solidity built-in variables and functions */
#include <stddef.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include "solidity_types.h"

/* msg variables */
uint256_t msg_data;
address_t msg_sender;
__uint32_t msg_sig;
uint256_t msg_value;

/* tx variables */
uint256_t tx_gasprice;
address_t tx_origin;

/* block variables */
uint256_t block_basefee;
uint256_t block_blobbasefee;
uint256_t block_chainid;
address_t block_coinbase;
uint256_t block_difficulty;
uint256_t block_gaslimit;
uint256_t block_number;
uint256_t block_prevrandao;
uint256_t block_timestamp;

/* blockhash — nondet abstraction (over-approximate) */
uint256_t blockhash(uint256_t x)
{
__ESBMC_HIDE:;
  uint256_t result;
  return result;
}

/* blobhash (EIP-4844) — nondet abstraction (over-approximate) */
uint256_t blobhash(uint256_t index)
{
__ESBMC_HIDE:;
  uint256_t result;
  return result;
}

/* gasleft */
unsigned int nondet_uint();

unsigned int _gaslimit;
void gasConsume()
{
__ESBMC_HIDE:;
  unsigned int consumed = nondet_uint();
  __ESBMC_assume(consumed > 0 && consumed <= _gaslimit);
  _gaslimit -= consumed;
}
uint256_t gasleft()
{
__ESBMC_HIDE:;
  gasConsume(); // always less
  return (uint256_t)_gaslimit;
}

/* integer power: base**exp using binary exponentiation */
uint256_t sol_pow_uint(uint256_t base, uint256_t exp)
{
__ESBMC_HIDE:;
  uint256_t result = 1;
  while (exp > 0)
  {
    if (exp & 1)
      result *= base;
    base *= base;
    exp >>= 1;
  }
  return result;
}

/* math functions */
uint256_t addmod(uint256_t x, uint256_t y, uint256_t k)
{
__ESBMC_HIDE:;
	return (x + y) % k;
}

uint256_t mulmod(uint256_t x, uint256_t y, uint256_t k)
{
__ESBMC_HIDE:;
	return (x * y) % k;
}

/*
 * Cryptographic hash functions — deterministic abstraction.
 *
 * Each function is modeled as a simple deterministic transformation of its
 * input.  This provides:
 *  - Functional consistency: same input always yields the same output,
 *    so keccak256(x) == keccak256(x) is provable.
 *  - Injectivity: different inputs yield different outputs (bijective),
 *    so keccak256(a) == keccak256(b) iff a == b.
 *  - O(1) for the SMT solver: single bitvector operation, no arrays or loops.
 *
 * Trade-offs:
 *  - The concrete hash value is not computed; assertions about specific
 *    hash outputs (e.g. keccak256(0) == 0xc5d2...) will not be provable.
 *  - The abstraction is sound for equality-based reasoning (e.g. string
 *    comparison via keccak256(abi.encodePacked(s1)) == keccak256(...s2)).
 *
 * Each hash function uses a distinct transformation to ensure
 * keccak256(x) != sha256(x) for all x != 0.
 */
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

/*
 * llc_nondet_bytes — nondet abstraction for the `bytes memory data` component
 * of a low-level `.call()` / `.staticcall()` / `.delegatecall()` return.
 *
 * The returned BytesDynamic has `initialized = 1` so that init-checks pass.
 * All other fields (length, offset, capacity) are left uninitialized →
 * ESBMC treats them as fresh nondet symbols.  In particular, `data.length`
 * is fully unconstrained (any size_t value is possible).
 *
 * Over-approximation semantics (same as crypto hash abstraction):
 *  - Sound for safety: no real return-data outcome is excluded.
 *  - Possible false positives for properties that rely on the concrete
 *    content or on specific length values.
 */
BytesDynamic llc_nondet_bytes(void)
{
__ESBMC_HIDE:;
  BytesDynamic result;
  result.initialized = 1;
  return result;
}

/* selfdestruct */
void selfdestruct()
{
__ESBMC_HIDE:;
  exit(0);
}
