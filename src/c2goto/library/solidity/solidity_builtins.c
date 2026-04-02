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

/* abi function declarations */
uint256_t abi_encode();
uint256_t abi_encodePacked();
uint256_t abi_encodeWithSelector();
uint256_t abi_encodeWithSignature();
uint256_t abi_encodeCall();

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
 * Cryptographic hash functions — nondet over-approximation.
 *
 * Each call returns an unconstrained (nondet) value of the appropriate type.
 * This is sound for safety verification: any real execution is included in
 * the set of explored behaviours, so no real bug is missed (no false negatives).
 *
 * Limitations:
 *  - No functional consistency: keccak256(x) called twice may yield different
 *    nondet values, so properties that rely on "same input → same output"
 *    cannot be verified and may produce false positives (spurious counter-
 *    examples).
 *  - The concrete hash value is never computed; assertions about specific
 *    hash outputs (e.g. keccak256(0) == 0xc5d2...) will not be provable.
 */
uint256_t keccak256(uint256_t x)
{
__ESBMC_HIDE:;
  uint256_t result;
  return result;
}

uint256_t sha256(uint256_t x)
{
__ESBMC_HIDE:;
  uint256_t result;
  return result;
}

address_t ripemd160(uint256_t x)
{
__ESBMC_HIDE:;
  address_t result;
  return result;
}

address_t ecrecover(uint256_t hash, unsigned int v, uint256_t r, uint256_t s)
{
__ESBMC_HIDE:;
  address_t result;
  return result;
}

/* selfdestruct */
void selfdestruct()
{
__ESBMC_HIDE:;
  exit(0);
}
