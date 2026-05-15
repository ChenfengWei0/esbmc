// M3's lowering TARGET: struct-of-(native nested arrays) = the per-field
// decomposition.  This is what the commutation rewrites the above into.
// If this is SUCCESSFUL and fast, the rewrite target is well-formed and
// ESBMC's backend handles per-field native multi-dim select/store
// natively (no array_conv, no tuple flattener).
struct G { unsigned a[4][4]; unsigned b[4][4]; } g;
int main() {
  unsigned i, j, v;
  __ESBMC_assume(i < 4 && j < 4);
  g.a[i][j] = v;                               // <- per-field native store-chain
  __ESBMC_assert(g.a[i][j] == v, "round-trip");// <- per-field native select-chain
  return 0;
}
