// IR-abstract form of Solidity `mapping(uint=>mapping(uint=>S))`:
// nested array whose leaf is a struct (finite here; the solver-level
// nested-array-of-struct encoding is what matters, identical to the
// infinite case via Branch A).  This is the shape that aborts in the
// node flattener (bare smt_sort) when the leaf is a struct.
typedef struct { unsigned a; unsigned b; } S;
S g[4][4];
int main() {
  unsigned i, j, v;
  __ESBMC_assume(i < 4 && j < 4);
  g[i][j].a = v;
  __ESBMC_assert(g[i][j].a == v, "round-trip");
  return 0;
}
