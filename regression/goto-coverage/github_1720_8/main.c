#include <stdio.h>
#include <assert.h>
#include <stdbool.h>

bool func() { return nondet_bool(); }

int main()
{
  bool a = nondet_bool();
  bool b = true;
  if (b || func() && a && b)
  {
    assert(1);
  }
  else
    assert(0);
}
