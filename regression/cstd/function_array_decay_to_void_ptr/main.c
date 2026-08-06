#include <assert.h>

static int pointer_is_null(void *p)
{
  return p == 0;
}

int main(void)
{
  char buf[4];

  int is_null = pointer_is_null(buf);

  assert(!is_null);
  return 0;
}
