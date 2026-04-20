/* Solidity address management and contract object tracking */
#include <stddef.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include "solidity_types.h"

unsigned int nondet_uint();

__attribute__((annotate("__ESBMC_inf_size"))) address_t sol_addr_array[1];
__attribute__((annotate("__ESBMC_inf_size"))) void *sol_obj_array[1];
__attribute__((annotate("__ESBMC_inf_size"))) const char *sol_cname_array[1];
unsigned int sol_max_cnt;

/* EOA balance map: tracks ETH balance at addresses that are NOT modelled
 * as `_ESBMC_Object_<C>` instances (i.e. EOAs and any address payable
 * value the user constructs).  Without this, `transfer/send` to an
 * unknown address only debited sender->$balance and the recipient's ETH
 * was effectively burnt — which made order-sensitive properties on
 * recipient balances vacuous (the SolidiFI TOD pattern lives here).
 *
 * Parallel arrays in the same shape as sol_addr_array so the SMT
 * encoding stays uniform; lookup is linear in sol_eoa_max_cnt and
 * therefore needs --unwind ≥ number of distinct EOAs touched. */
__attribute__((annotate("__ESBMC_inf_size"))) address_t sol_eoa_addr_array[1];
__attribute__((annotate("__ESBMC_inf_size"))) uint256_t sol_eoa_balance_array[1];
unsigned int sol_eoa_max_cnt;

int _ESBMC_get_addr_array_idx(address_t tgt)
{
__ESBMC_HIDE:;
  if (tgt == (address_t)0)
    return -1;

  for (unsigned int i = 0; i < sol_max_cnt; i++)
  {
    if ((address_t)sol_addr_array[i] == (address_t)tgt)
      return i;
  }
  return -1;
}
bool _ESBMC_cmp_cname(const char *c_1, const char *c_2)
{
__ESBMC_HIDE:;
  return c_1 == c_2;
}
void *_ESBMC_get_obj(address_t addr, const char *cname)
{
__ESBMC_HIDE:;
  int idx = _ESBMC_get_addr_array_idx(addr);
  if (idx == -1)
    // this means it's not previously stored
    return NULL;
  if (_ESBMC_cmp_cname(sol_cname_array[idx], cname))
    return sol_obj_array[idx];
  return NULL;
}
void update_addr_obj(address_t addr, void *obj, const char *cname)
{
__ESBMC_HIDE:;
  // __ESBMC_assume(obj != NULL);
  sol_addr_array[sol_max_cnt] = addr;
  sol_obj_array[sol_max_cnt] = obj;
  sol_cname_array[sol_max_cnt] = cname;
  ++sol_max_cnt;
}
address_t _ESBMC_get_unique_address(void *obj, const char *cname)
{
__ESBMC_HIDE:;
  // __ESBMC_assume(obj != NULL);
  address_t tmp;
  do
  {
    tmp = (address_t)nondet_uint();
    if (tmp == (address_t)0)
      continue;
    if (sol_max_cnt == 0)
      break;
  } while (_ESBMC_get_addr_array_idx(tmp) == -1);

  update_addr_obj(tmp, obj, cname);
  return tmp;
}
const char *_ESBMC_get_nondet_cont_name(const char *c_array[], unsigned int len)
{
__ESBMC_HIDE:;
  unsigned int rand = nondet_uint() % len;
  return c_array[rand];
}

/* EOA balance lookup. Returns slot index or -1 if address is not yet
 * tracked. Linear scan; caller paths must --unwind at least enough to
 * cover sol_eoa_max_cnt iterations. */
int _ESBMC_eoa_get_idx(address_t addr)
{
__ESBMC_HIDE:;
    for (unsigned int i = 0; i < sol_eoa_max_cnt; i++)
    {
        if (sol_eoa_addr_array[i] == addr)
            return (int)i;
    }
    return -1;
}

/* Find or insert. On insert, the new slot's initial balance is nondet
 * (sound over-approximation: a real EOA could have any pre-existing
 * balance). User-side tests that need a deterministic starting point
 * should `require(addr.balance == 0)` (or any other constant) before
 * the first transfer.
 *
 * NB: We do NOT special-case address(0). Solidity's `transfer` reverts
 * on insufficient balance and most contracts forbid sending to 0x0 via
 * an explicit require, but the EVM itself permits it (the ETH becomes
 * unspendable). Mirroring that behaviour keeps our model conservative. */
unsigned int _ESBMC_eoa_get_or_init(address_t addr)
{
__ESBMC_HIDE:;
    int idx = _ESBMC_eoa_get_idx(addr);
    if (idx != -1)
        return (unsigned int)idx;
    unsigned int new_idx = sol_eoa_max_cnt;
    sol_eoa_addr_array[new_idx] = addr;
    sol_eoa_balance_array[new_idx] = (uint256_t)nondet_uint();
    ++sol_eoa_max_cnt;
    return new_idx;
}

void _ESBMC_eoa_credit(address_t addr, uint256_t val)
{
__ESBMC_HIDE:;
    unsigned int idx = _ESBMC_eoa_get_or_init(addr);
    sol_eoa_balance_array[idx] += val;
}

uint256_t _ESBMC_eoa_balance_of(address_t addr)
{
__ESBMC_HIDE:;
    unsigned int idx = _ESBMC_eoa_get_or_init(addr);
    return sol_eoa_balance_array[idx];
}
