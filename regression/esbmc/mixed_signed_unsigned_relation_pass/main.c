#include <assert.h>

int nondet_int(void);
unsigned int nondet_uint(void);

int main(void)
{
  int s = nondet_int();
  unsigned int u = nondet_uint();

  __ESBMC_assume(s == -1);
  __ESBMC_assume(u == 1);

  assert(!(s < u));
  assert(!(u > s));

  return 0;
}
