/* Solidity miscellaneous: min/max, reentrancy check, state initialization */
#include <stddef.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include "solidity_types.h"

unsigned int nondet_uint();

extern uint256_t msg_data;
extern address_t msg_sender;
extern uint32_t msg_sig;
extern uint256_t msg_value;
extern address_t _ESBMC_enclosing_contract_address;
extern void *_ESBMC_enclosing_contract_this;
extern uint256_t tx_gasprice;
extern address_t tx_origin;
extern uint256_t block_basefee;
extern uint256_t block_blobbasefee;
extern uint256_t block_chainid;
extern address_t block_coinbase;
extern uint256_t block_difficulty;
extern uint256_t block_gaslimit;
extern uint256_t block_number;
extern uint256_t block_prevrandao;
extern uint256_t block_timestamp;
extern unsigned int _gaslimit;
extern unsigned int sol_max_cnt;
extern unsigned int sol_eoa_max_cnt;
extern unsigned int esbmc_array_count;

uint256_t _max(unsigned int bitwidth, bool is_signed)
{
__ESBMC_HIDE:;
  __ESBMC_assume(bitwidth > 0 && bitwidth <= 256);
  if (is_signed)
  {
    return ((uint256_t)1 << (bitwidth - 1)) - (uint256_t)1;
  }
  else
  {
    if (bitwidth == 256)
    {
      return (uint256_t)-1;
    }
    return ((uint256_t)1 << bitwidth) - (uint256_t)1;
  }
}

int256_t _min(unsigned int bitwidth, bool is_signed)
{
__ESBMC_HIDE:;
  if (is_signed)
  {
    __ESBMC_assume(bitwidth > 0 && bitwidth <= 256);
    return -((int256_t)1 << (bitwidth - 1)); // -2^(N-1)
  }
  else
  {
    return (int256_t)0; // Min of unsigned is always 0
  }
}

unsigned int _creationCode()
{
__ESBMC_HIDE:;
  return nondet_uint();
}

unsigned int _runtimeCode()
{
__ESBMC_HIDE:;
  return nondet_uint();
}

/* type(I).interfaceId — nondet over-approximation (bytes4) */
uint32_t _interfaceId()
{
__ESBMC_HIDE:;
  return (uint32_t)nondet_uint();
}

void _ESBMC_check_reentrancy(const bool _ESBMC_mutex)
{
__ESBMC_HIDE:;
  if (_ESBMC_mutex)
    assert(!"Reentrancy behavior detected");
}

void initialize()
{
__ESBMC_HIDE:;
  // we assume it starts from an EOA
  msg_data = (uint256_t)nondet_uint();
  msg_sender = (address_t)nondet_uint();
  msg_sig = nondet_uint();
  msg_value = (uint256_t)nondet_uint();

  tx_gasprice = (uint256_t)nondet_uint();
  // this can only be an EOA's address
  tx_origin = (address_t)nondet_uint();

  block_basefee = (uint256_t)nondet_uint();
  block_blobbasefee = (uint256_t)nondet_uint();
  block_chainid = (uint256_t)nondet_uint();
  block_coinbase = (address_t)nondet_uint();
  block_difficulty = (uint256_t)nondet_uint();
  block_gaslimit = (uint256_t)nondet_uint();
  block_number = (uint256_t)nondet_uint();
  block_prevrandao = (uint256_t)nondet_uint();
  block_timestamp = (uint256_t)nondet_uint();

  _gaslimit = nondet_uint();

  sol_max_cnt = 0;
  esbmc_array_count = 0;
}

/* Per-tx ambient reseed. Called from the per-contract dispatcher
 * while-loop prologue (_ESBMC_Main_<C>) so each iteration models a
 * distinct transaction with its own sender / value / block context.
 * block.number and block.timestamp are constrained non-decreasing
 * (real EVM is monotone). msg_sig, block_chainid, _gaslimit are NOT
 * touched: msg_sig is set by per-method dispatch, chainid is
 * chain-constant, _gaslimit is a per-call intrinsic via gasleft().
 * The constructor's binding `owner = msg.sender` runs BEFORE the
 * first call to this helper, so the deployer identity stays stored
 * and per-iter senders are properly distinct. */
void _sol_per_tx_reseed()
{
__ESBMC_HIDE:;
  /* tx-envelope state */
  msg_data    = nondet_uint256();
  msg_sender  = (address_t)nondet_uint();
  msg_value   = nondet_uint256();
  tx_origin   = (address_t)nondet_uint();
  tx_gasprice = nondet_uint256();

  /* Top-level call: tx.origin == msg.sender (real EVM invariant for
   * any direct EOA→contract call).  The bound-mode dispatcher only
   * drives top-level calls — nested contract-to-contract calls don't
   * reseed and keep the same msg.sender as their caller — so this
   * assume is sound at the dispatcher boundary.  Sound for safety:
   * narrows the state space; bugs reachable only via the disallowed
   * `tx.origin != msg.sender` path (contract-to-contract reentry from
   * outside the harness) aren't explored anyway. */
  __ESBMC_assume(tx_origin == msg_sender);

  /* block state — monotonic on number / timestamp */
  uint256_t _new_bn = nondet_uint256();
  __ESBMC_assume(_new_bn >= block_number);
  block_number = _new_bn;

  uint256_t _new_ts = nondet_uint256();
  __ESBMC_assume(_new_ts >= block_timestamp);
  block_timestamp = _new_ts;

  block_basefee     = nondet_uint256();
  block_blobbasefee = nondet_uint256();
  block_coinbase    = (address_t)nondet_uint();
  block_difficulty  = nondet_uint256();
  block_gaslimit    = nondet_uint256();
  block_prevrandao  = nondet_uint256();
}
