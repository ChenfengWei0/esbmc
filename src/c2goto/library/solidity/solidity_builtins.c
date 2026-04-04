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

/*
 * Modular arithmetic — arbitrary precision per Solidity spec.
 *
 * Solidity specifies that addmod/mulmod perform the intermediate
 * addition/multiplication with arbitrary precision (no wrap at 2^256).
 * We use a 512-bit intermediate type to avoid overflow.
 */
typedef unsigned BIGINT(512) uint512_t;

uint256_t addmod(uint256_t x, uint256_t y, uint256_t k)
{
__ESBMC_HIDE:;
  uint512_t wide = (uint512_t)x + (uint512_t)y;
  return (uint256_t)(wide % (uint512_t)k);
}

uint256_t mulmod(uint256_t x, uint256_t y, uint256_t k)
{
__ESBMC_HIDE:;
  uint512_t wide = (uint512_t)x * (uint512_t)y;
  return (uint256_t)(wide % (uint512_t)k);
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
